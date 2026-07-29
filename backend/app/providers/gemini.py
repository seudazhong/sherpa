"""Native Google Gemini `generateContent` provider (ADR-041).

Talks to Gemini's `:streamGenerateContent?alt=sse` and normalizes its parts-based stream
into the loop's event vocabulary. OpenAI-shape `messages` are translated to Gemini
`contents` (`user`/`model` roles, `functionCall`/`functionResponse` parts), the system prompt
becomes `systemInstruction`, and tools become `functionDeclarations` with a Gemini-sanitized
JSON Schema (see `providers/tools.py`). Gemini does not stream tool-call arguments (a single
`functionCall` part), does not issue tool-call ids (we synthesize `call_{n}`), and marks
reasoning parts with `thought: true`. The API key is sent only in the `x-goog-api-key` header.
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
from app.providers.tools import to_gemini_tools
from app.security.redaction import redact

_DEFAULT_BASE = "https://generativelanguage.googleapis.com"

_STOP: dict[str, StopReason] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
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


def _gemini_user_parts(content: object) -> list[dict[str, Any]]:
    """User content → Gemini parts (`text` / `inlineData` image; ADR-043)."""
    parts: list[dict[str, Any]] = []
    for block in normalize_content(content):
        if isinstance(block, ImageBlock):
            parts.append({"inlineData": {"mimeType": block.media_type, "data": block.data_b64}})
        else:
            parts.append({"text": block.text})
    return parts or [{"text": ""}]


def _translate(messages: list[Message]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """OpenAI-shape messages → (systemInstruction, gemini contents)."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    # id → tool name, so a tool_result (which carries only tool_call_id) can name its
    # functionResponse (Gemini requires the name).
    id_to_name: dict[str, str] = {}
    for m in messages:
        for tc in _tool_calls(m):
            fn = tc.get("function") or {}
            id_to_name[str(tc.get("id", ""))] = str(fn.get("name", ""))

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                system_parts.append(str(content))
            continue
        if role == "tool":
            call_id = str(m.get("tool_call_id", ""))
            name = id_to_name.get(call_id, call_id)
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"result": str(content or "")},
                            }
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            parts: list[dict[str, Any]] = []
            if content:
                parts.append({"text": str(content)})
            for tc in _tool_calls(m):
                fn = tc.get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
                except json.JSONDecodeError:
                    args = {}
                parts.append(
                    {
                        "functionCall": {
                            "name": str(fn.get("name", "")),
                            "args": args if isinstance(args, dict) else {},
                        }
                    }
                )
            contents.append({"role": "model", "parts": parts or [{"text": " "}]})
            continue
        contents.append({"role": "user", "parts": _gemini_user_parts(content)})

    system = {"parts": [{"text": "\n".join(system_parts)}]} if system_parts else None
    return system, contents


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or _DEFAULT_BASE).rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._transport = transport

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        system, contents = _translate(messages)
        payload: dict[str, object] = {"contents": contents}
        if system is not None:
            payload["systemInstruction"] = system
        gem_tools = to_gemini_tools(tools)
        if gem_tools is not None:
            payload["tools"] = gem_tools
        mdl = model or self._model
        url = f"{self._base_url}/v1beta/models/{mdl}:streamGenerateContent?alt=sse"
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}

        stop: StopReason | None = None
        saw_tool = False
        call_n = 0
        input_tokens: int | None = None
        output_tokens: int | None = None

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise ProviderError(
                        "gemini generateContent call failed",
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

                    usage = chunk.get("usageMetadata") or {}
                    if isinstance(usage.get("promptTokenCount"), int):
                        input_tokens = usage["promptTokenCount"]
                    if isinstance(usage.get("candidatesTokenCount"), int):
                        output_tokens = usage["candidatesTokenCount"]

                    for cand in chunk.get("candidates") or []:
                        for part in (cand.get("content") or {}).get("parts") or []:
                            fc = part.get("functionCall")
                            if fc:
                                saw_tool = True
                                yield ToolCall(
                                    id=f"call_{call_n}",
                                    name=str(fc.get("name", "")),
                                    args=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                                )
                                call_n += 1
                            elif "text" in part:
                                text = str(part.get("text", ""))
                                if part.get("thought") is True:
                                    yield ReasoningDelta(text)
                                elif text:
                                    yield TextDelta(text)
                        fr = cand.get("finishReason")
                        if fr:
                            stop = _STOP.get(str(fr), "stop")

        if saw_tool:
            stop = "tool_use"
        yield Finish(stop or "stop", input_tokens=input_tokens, output_tokens=output_tokens)
