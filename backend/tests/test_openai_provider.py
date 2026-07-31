"""OpenAI-compatible provider (m2-provider): SSE normalization, no network.

Uses httpx.MockTransport to feed canned Chat Completions stream chunks and
asserts they normalize to the loop's event vocabulary.
"""

from __future__ import annotations

import httpx
import pytest

from app.providers import Finish, OpenAICompatibleProvider, ProviderError, TextDelta, ToolCall


def _sse(*chunks: str) -> str:
    return "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _provider(body: str) -> OpenAICompatibleProvider:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return OpenAICompatibleProvider(
        base_url="http://proxy",
        api_key="k",
        model="claude-sonnet-4.6",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_text_stream_normalizes_to_deltas_and_finish() -> None:
    body = _sse(
        '{"choices":[{"delta":{"role":"assistant","content":""}}]}',
        '{"choices":[{"delta":{"content":"Hello"}}]}',
        '{"choices":[{"delta":{"content":" world"}}]}',
        '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
    )
    events = [e async for e in _provider(body).stream(messages=[{"role": "user", "content": "hi"}])]

    texts = [e.text for e in events if isinstance(e, TextDelta)]
    finishes = [e for e in events if isinstance(e, Finish)]
    assert "".join(texts) == "Hello world"
    assert len(finishes) == 1 and finishes[0].stop_reason == "stop"


@pytest.mark.asyncio
async def test_tool_call_stream_assembles_and_maps_stop_reason() -> None:
    body = _sse(
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"core_get_time","arguments":""}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}',
        '{"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    )
    events = [e async for e in _provider(body).stream(messages=[{"role": "user", "content": "t"}])]

    calls = [e for e in events if isinstance(e, ToolCall)]
    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(calls) == 1
    assert calls[0].id == "c1" and calls[0].name == "core_get_time" and calls[0].args == {}
    assert finishes[0].stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_usage_chunk_populates_finish_tokens_and_requests_include_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        body = _sse(
            '{"choices":[{"delta":{"content":"hi"}}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
            '{"choices":[],"usage":{"prompt_tokens":42,"completion_tokens":7,"total_tokens":49}}',
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = OpenAICompatibleProvider(
        base_url="http://proxy",
        api_key="k",
        model="claude-sonnet-4.6",
        transport=httpx.MockTransport(handler),
    )
    events = [e async for e in provider.stream(messages=[{"role": "user", "content": "hi"}])]

    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(finishes) == 1
    assert finishes[0].stop_reason == "stop"
    assert finishes[0].input_tokens == 42
    assert finishes[0].output_tokens == 7
    assert seen.get("stream_options") == {"include_usage": True}


@pytest.mark.asyncio
async def test_http_error_raises_provider_error_with_redacted_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "No connected db", "api_key": "sk-should-be-hidden"}},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://proxy",
        api_key="k",
        model="claude-sonnet-4.6",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as ei:
        _ = [e async for e in provider.stream(messages=[{"role": "user", "content": "hi"}])]

    err = ei.value
    assert err.status_code == 400
    assert err.body is not None
    # The real reason is preserved for logs/journal…
    assert "No connected db" in err.body
    # …but any secret-named field in the body is redacted.
    assert "sk-should-be-hidden" not in err.body
    assert "REDACTED" in err.body
    # detail() is a compact one-liner carrying status + body.
    d = err.detail()
    assert "status=400" in d and "No connected db" in d
