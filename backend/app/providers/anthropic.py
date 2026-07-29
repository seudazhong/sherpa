"""Native Anthropic Messages API provider (ADR-041).

Talks to Anthropic's `/v1/messages` (or an Anthropic-compatible endpoint, e.g. Kimi/MiniMax)
and normalizes its **block-based** SSE into the loop's event vocabulary. The OpenAI-shape
`messages` the loop assembles are translated to Anthropic's format: the system prompt is a
top-level field, tool results ride in a `user` message as `tool_result` blocks, assistant
`tool_calls` become `tool_use` blocks, and consecutive same-role messages are merged
(Anthropic rejects them otherwise). `max_tokens` is mandatory. Streaming events:
`content_block_delta` → text_delta→TextDelta / thinking_delta→ReasoningDelta /
input_json_delta→(accumulate tool args) → ToolCall on `content_block_stop`; `message_delta`
carries `stop_reason` + output tokens. The API key is sent only in the `x-api-key` header.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.providers.base import (
    Finish,
    Message,
    ProviderError,
    ProviderEvent,
    ReasoningDelta,
    StopReason,
    TextDelta,
    ToolCall,
    ToolSchema,
)
from app.providers.content import ImageBlock, normalize_content
from app.providers.tools import to_anthropic_tools
from app.security.redaction import redact

_DEFAULT_BASE = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096

_STOP: dict[str, StopReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_use",
    "max_tokens": "length",
}


def _error_detail(resp: httpx.Response, *, limit: int = 1000) -> str:
    try:
        text = resp.text
    except Exception:
        return ""
    try:
        text = json.dumps(redact(json.loads(text)), separators=(",", ":"), ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    return text[:limit]


def _tool_calls(m: Message) -> list[dict[str, Any]]:
    tcs = m.get("tool_calls")
    return [tc for tc in tcs if isinstance(tc, dict)] if isinstance(tcs, list) else []


def _anthropic_user_blocks(content: object) -> list[dict[str, Any]]:
    """User content → Anthropic blocks (`text` / base64 `image`; ADR-043)."""
    blocks: list[dict[str, Any]] = []
    for block in normalize_content(content):
        if isinstance(block, ImageBlock):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.media_type,
                        "data": block.data_b64,
                    },
                }
            )
        else:
            blocks.append({"type": "text", "text": block.text})
    return blocks or [{"type": "text", "text": ""}]


def _translate(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-shape messages → (system_prompt, anthropic_messages)."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                system_parts.append(str(content))
            continue
        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(m.get("tool_call_id", "")),
                            "content": str(content or ""),
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in _tool_calls(m):
                fn = tc.get("function") or {}
                raw = fn.get("arguments")
                try:
                    inp = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
                except json.JSONDecodeError:
                    inp = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(tc.get("id", "")),
                        "name": str(fn.get("name", "")),
                        "input": inp if isinstance(inp, dict) else {},
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": " "}]})
            continue
        out.append({"role": "user", "content": _anthropic_user_blocks(content)})

    merged: list[dict[str, Any]] = []
    for msg in out:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"].extend(msg["content"])
        else:
            merged.append(msg)
    return "\n".join(system_parts), merged


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or _DEFAULT_BASE).rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._transport = transport

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        system, anthropic_messages = _translate(messages)
        payload: dict[str, object] = {
            "model": model or self._model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_messages,
            "stream": True,
        }
        if system:
            payload["system"] = system
        anth_tools = to_anthropic_tools(tools)
        if anth_tools is not None:
            payload["tools"] = anth_tools
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        tool_blocks: dict[int, dict[str, str]] = {}
        stop: StopReason | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            async with client.stream(
                "POST", f"{self._base_url}/v1/messages", json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise ProviderError(
                        "anthropic messages call failed",
                        status_code=resp.status_code,
                        body=_error_detail(resp),
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    t = chunk.get("type")
                    if t == "message_start":
                        u = (chunk.get("message") or {}).get("usage") or {}
                        if isinstance(u.get("input_tokens"), int):
                            input_tokens = u["input_tokens"]
                    elif t == "content_block_start":
                        cb = chunk.get("content_block") or {}
                        if cb.get("type") == "tool_use":
                            tool_blocks[int(chunk.get("index", 0))] = {
                                "id": str(cb.get("id", "")),
                                "name": str(cb.get("name", "")),
                                "json": "",
                            }
                    elif t == "content_block_delta":
                        d = chunk.get("delta") or {}
                        dt = d.get("type")
                        if dt == "text_delta":
                            yield TextDelta(str(d.get("text", "")))
                        elif dt == "thinking_delta":
                            yield ReasoningDelta(str(d.get("thinking", "")))
                        elif dt == "input_json_delta":
                            idx = int(chunk.get("index", 0))
                            if idx in tool_blocks:
                                tool_blocks[idx]["json"] += str(d.get("partial_json", ""))
                    elif t == "content_block_stop":
                        idx = int(chunk.get("index", 0))
                        if idx in tool_blocks:
                            tb = tool_blocks[idx]
                            try:
                                args = json.loads(tb["json"]) if tb["json"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCall(
                                id=tb["id"] or f"call_{idx}",
                                name=tb["name"],
                                args=args if isinstance(args, dict) else {},
                            )
                    elif t == "message_delta":
                        d = chunk.get("delta") or {}
                        sr = d.get("stop_reason")
                        if sr:
                            stop = _STOP.get(str(sr), "stop")
                        u = chunk.get("usage") or {}
                        if isinstance(u.get("output_tokens"), int):
                            output_tokens = u["output_tokens"]
                    elif t == "message_stop":
                        break

        yield Finish(stop or "stop", input_tokens=input_tokens, output_tokens=output_tokens)
