"""Mock provider: default echo + scripted multi-turn (no DB/network)."""

from __future__ import annotations

import pytest

from app.providers import Finish, MockProvider, TextDelta, ToolCall


@pytest.mark.asyncio
async def test_mock_default_echoes_last_user() -> None:
    provider = MockProvider()
    events = [e async for e in provider.stream(messages=[{"role": "user", "content": "hi"}])]
    assert events == [TextDelta("echo: hi"), Finish("stop")]


@pytest.mark.asyncio
async def test_mock_scripted_tool_then_answer() -> None:
    provider = MockProvider(
        script=[
            [ToolCall(id="c1", name="read", args={"path": "x"}), Finish("tool_use")],
            [TextDelta("done"), Finish("stop")],
        ]
    )
    turn1 = [
        e
        async for e in provider.stream(
            messages=[{"role": "user", "content": "go"}], tools=[{"name": "read"}]
        )
    ]
    assert isinstance(turn1[0], ToolCall)
    assert turn1[-1] == Finish("tool_use")

    turn2 = [e async for e in provider.stream(messages=[{"role": "user", "content": "go"}])]
    assert turn2 == [TextDelta("done"), Finish("stop")]
