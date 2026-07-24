"""OpenAI-compatible streaming provider (docs/08 §5).

Talks to any OpenAI Chat Completions endpoint. v1 targets the local litellm
proxy that forwards GitHub Copilot models. SSE chunks are normalized into the
loop's event vocabulary: content deltas -> TextDelta, streamed tool_calls ->
ToolCall (assembled by index), finish_reason -> Finish. The api key is only sent
in the Authorization header and never placed in events or logs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import (
    Finish,
    Message,
    ProviderEvent,
    StopReason,
    TextDelta,
    ToolCall,
    ToolSchema,
)

_FINISH: dict[str, StopReason] = {
    "stop": "stop",
    "tool_calls": "tool_use",
    "length": "length",
}


def _to_openai_tools(tools: list[ToolSchema] | None) -> list[dict[str, object]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object"}),
            },
        }
        for t in tools
    ]


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
        oa_tools = _to_openai_tools(tools)
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
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                resp.raise_for_status()
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
