"""Deterministic mock provider for dev + tests (no real model calls).

Either replays an explicit per-call `script`, or (unscripted) echoes the last
user message and stops. Keeps the loop testable without network or a model key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.providers.base import Finish, Message, ProviderEvent, TextDelta, ToolSchema


def _default_turn(messages: list[Message]) -> list[ProviderEvent]:
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    text = f"echo: {last_user['content']}" if last_user is not None else "ready"
    return [TextDelta(str(text)), Finish("stop")]


class MockProvider:
    name = "mock"

    def __init__(self, script: Sequence[Sequence[ProviderEvent]] | None = None) -> None:
        self._script: list[list[ProviderEvent]] = [list(turn) for turn in (script or [])]
        self._call = 0

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        turn = (
            self._script[self._call] if self._call < len(self._script) else _default_turn(messages)
        )
        self._call += 1
        for event in turn:
            yield event
