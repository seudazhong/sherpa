"""Provider adapters (ADR-041, MP.2): tool-schema serializers + openai_compatible reasoning
/ per-choice usage + native Anthropic & Gemini message translation and SSE normalization.

No network — httpx.MockTransport feeds canned streams.
"""

from __future__ import annotations

import httpx
import pytest

from app.providers import (
    AnthropicProvider,
    Finish,
    GeminiProvider,
    OpenAICompatibleProvider,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
from app.providers.anthropic import _translate as anth_translate
from app.providers.gemini import _translate as gem_translate
from app.providers.tools import to_anthropic_tools, to_gemini_tools, to_openai_tools

_TOOLS = [
    {
        "name": "core_get_time",
        "description": "get the time",
        "input_schema": {
            "type": "object",
            "properties": {"tz": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
    }
]


def _sse(*chunks: str) -> str:
    return "".join(f"data: {c}\n\n" for c in chunks)


def _mock(body: str, cls, **kw):  # type: ignore[no-untyped-def]
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return cls(transport=httpx.MockTransport(handler), **kw)


# --- tool serializers -------------------------------------------------------


def test_tool_serializers_shapes_and_gemini_sanitize() -> None:
    oa = to_openai_tools(_TOOLS)
    assert oa[0]["type"] == "function" and oa[0]["function"]["name"] == "core_get_time"

    an = to_anthropic_tools(_TOOLS)
    assert an[0]["name"] == "core_get_time" and "input_schema" in an[0]

    gm = to_gemini_tools(_TOOLS)
    decl = gm[0]["functionDeclarations"][0]
    params = decl["parameters"]
    # list type coerced to a single string; additionalProperties dropped.
    assert params["properties"]["tz"]["type"] == "string"
    assert "additionalProperties" not in params

    # Empty-parameter tool → `parameters` omitted (Gemini rejects {}).
    empty = to_gemini_tools(
        [{"name": "ping", "description": "", "input_schema": {"type": "object"}}]
    )
    assert "parameters" not in empty[0]["functionDeclarations"][0]


# --- openai_compatible enhancements -----------------------------------------


@pytest.mark.asyncio
async def test_openai_reasoning_delta_and_per_choice_usage() -> None:
    body = (
        _sse(
            '{"choices":[{"delta":{"reasoning_content":"thinking..."}}]}',
            '{"choices":[{"delta":{"content":"answer"}}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop","usage":{"prompt_tokens":9,"completion_tokens":3}}]}',
        )
        + "data: [DONE]\n\n"
    )
    p = _mock(body, OpenAICompatibleProvider, base_url="http://x", api_key="k", model="m")
    events = [e async for e in p.stream(messages=[{"role": "user", "content": "hi"}])]
    assert [e.text for e in events if isinstance(e, ReasoningDelta)] == ["thinking..."]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "answer"
    fin = next(e for e in events if isinstance(e, Finish))
    assert fin.input_tokens == 9 and fin.output_tokens == 3


# --- Anthropic native -------------------------------------------------------


def test_anthropic_translate_system_toolresult_merge() -> None:
    system, msgs = anth_translate(
        [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "tu1", "function": {"name": "core_get_time", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "tu1", "content": "12:00"},
            {"role": "user", "content": "thanks"},
        ]
    )
    assert system == "be nice"
    # assistant tool_use block
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use" and msgs[1]["content"][0]["id"] == "tu1"
    # tool result + following user merged into one user message (consecutive same role)
    assert msgs[2]["role"] == "user"
    kinds = [b["type"] for b in msgs[2]["content"]]
    assert kinds == ["tool_result", "text"]


@pytest.mark.asyncio
async def test_anthropic_stream_normalizes_blocks() -> None:
    body = _sse(
        '{"type":"message_start","message":{"usage":{"input_tokens":10}}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hmm"}}',
        '{"type":"content_block_stop","index":0}',
        '{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu1","name":"core_get_time"}}',
        '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{}"}}',
        '{"type":"content_block_stop","index":1}',
        '{"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":5}}',
        '{"type":"message_stop"}',
    )
    p = _mock(body, AnthropicProvider, api_key="k", model="claude-x")
    events = [e async for e in p.stream(messages=[{"role": "user", "content": "hi"}], tools=_TOOLS)]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hi"
    assert [e.text for e in events if isinstance(e, ReasoningDelta)] == ["hmm"]
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert calls[0].id == "tu1" and calls[0].name == "core_get_time" and calls[0].args == {}
    fin = next(e for e in events if isinstance(e, Finish))
    assert fin.stop_reason == "tool_use" and fin.input_tokens == 10 and fin.output_tokens == 5


# --- Gemini native ----------------------------------------------------------


def test_gemini_translate_system_and_functionresponse_name() -> None:
    system, contents = gem_translate(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_0", "function": {"name": "core_get_time", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_0", "content": "12:00"},
        ]
    )
    assert system == {"parts": [{"text": "sys"}]}
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "core_get_time"
    # tool result → functionResponse carrying the resolved name (from id map)
    fr = contents[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "core_get_time" and fr["response"] == {"result": "12:00"}


@pytest.mark.asyncio
async def test_gemini_stream_normalizes_parts() -> None:
    body = _sse(
        '{"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}',
        '{"candidates":[{"content":{"parts":[{"text":"lo"}]}}]}',
        '{"candidates":[{"content":{"parts":[{"text":"why","thought":true}]}}]}',
        '{"candidates":[{"content":{"parts":[{"functionCall":{"name":"core_get_time","args":{}}}]},'
        '"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":4}}',
    )
    p = _mock(body, GeminiProvider, api_key="k", model="gemini-x")
    events = [e async for e in p.stream(messages=[{"role": "user", "content": "hi"}], tools=_TOOLS)]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello"
    assert [e.text for e in events if isinstance(e, ReasoningDelta)] == ["why"]
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert calls[0].id == "call_0" and calls[0].name == "core_get_time" and calls[0].args == {}
    fin = next(e for e in events if isinstance(e, Finish))
    assert fin.stop_reason == "tool_use" and fin.input_tokens == 12 and fin.output_tokens == 4
