"""OpenAI-compatible streaming provider (docs/08 §5).

Talks to any OpenAI Chat Completions endpoint. v1 targets the local litellm
proxy that forwards GitHub Copilot models. SSE chunks are normalized into the
loop's event vocabulary: content deltas -> TextDelta, streamed tool_calls ->
ToolCall (assembled by index), finish_reason -> Finish. The api key is only sent
in the Authorization header and never placed in events or logs.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

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
from app.providers.tools import to_openai_tools
from app.security.redaction import redact

_FINISH: dict[str, StopReason] = {
    "stop": "stop",
    "tool_calls": "tool_use",
    "length": "length",
}


def openai_endpoint(base_url: str, suffix: str) -> str:
    """Join an OpenAI-compatible ``base_url`` with an API ``suffix`` (e.g.
    ``chat/completions`` / ``models``), tolerating both conventions: a root without a
    version (``http://proxy:4000`` → ``…/v1/<suffix>``) and a base that already includes the
    version segment (``https://api.openai.com/v1`` / ``…/openai/v1`` → ``…/<suffix>``)."""
    b = base_url.rstrip("/")
    if re.search(r"/v\d+$", b) or "/v1/" in b or b.endswith("/openai"):
        return f"{b}/{suffix}"
    return f"{b}/v1/{suffix}"


def _error_detail(resp: httpx.Response, *, limit: int = 1000) -> str:
    """Bounded, redacted body of a failed provider response (safe for logs).

    Requires the body to already be read (`await resp.aread()`), since streaming
    responses do not expose `.text` until then. JSON is redacted key-wise; any
    body is truncated to `limit` chars.
    """
    try:
        text = resp.text
    except Exception:  # body not readable
        return ""
    try:
        parsed = redact(json.loads(text))
        text = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    return text[:limit]


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._transport = transport  # for tests (httpx.MockTransport)

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        payload: dict[str, object] = {
            "model": model or self._model,
            "messages": messages,
            "stream": True,
            # Ask the endpoint for a final usage chunk (choices: [], usage: {...}).
            "stream_options": {"include_usage": True},
        }
        oa_tools = to_openai_tools(tools)
        if oa_tools is not None:
            payload["tools"] = oa_tools
        headers = {"Authorization": f"Bearer {self._api_key}"}

        tool_acc: dict[int, dict[str, str]] = {}
        pending_stop: StopReason | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            async with client.stream(
                "POST",
                openai_endpoint(self._base_url, "chat/completions"),
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise ProviderError(
                        "provider chat completion failed",
                        status_code=resp.status_code,
                        body=_error_detail(resp),
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # Usage arrives on its own chunk (usually choices: []); parse it
                    # before the choices guard so the final usage chunk is not skipped.
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
                        if isinstance(pt, int):
                            input_tokens = pt
                        if isinstance(ct, int):
                            output_tokens = ct

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    # Some OpenAI-compatible providers (MoonshotAI) report usage per choice.
                    ch_usage = choice.get("usage")
                    if isinstance(ch_usage, dict):
                        pt, ct = ch_usage.get("prompt_tokens"), ch_usage.get("completion_tokens")
                        if isinstance(pt, int):
                            input_tokens = pt
                        if isinstance(ct, int):
                            output_tokens = ct

                    # Reasoning models expose chain-of-thought in a side field, named
                    # `reasoning_content` (DeepSeek/QwQ) or `reasoning` (Groq/OpenRouter).
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield ReasoningDelta(str(reasoning))

                    content = delta.get("content")
                    if content:
                        yield TextDelta(str(content))

                    for tc in delta.get("tool_calls") or []:
                        acc = tool_acc.setdefault(
                            int(tc.get("index", 0)), {"id": "", "name": "", "args": ""}
                        )
                        if tc.get("id"):
                            acc["id"] = str(tc["id"])
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] = str(fn["name"])
                        if fn.get("arguments"):
                            acc["args"] += str(fn["arguments"])

                    finish = choice.get("finish_reason")
                    if finish:
                        for idx, acc in sorted(tool_acc.items()):
                            if not acc["name"]:
                                continue
                            try:
                                args = json.loads(acc["args"]) if acc["args"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCall(
                                id=acc["id"] or f"call_{idx}", name=acc["name"], args=args
                            )
                        # Defer Finish until the stream ends so it carries the usage
                        # chunk (which follows finish_reason).
                        pending_stop = _FINISH.get(str(finish), "stop")

        yield Finish(
            pending_stop or "stop",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
