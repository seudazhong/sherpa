"""Provider layer: one narrow interface; every model provider conforms (docs/08).

The loop speaks a single normalized event vocabulary and never sees raw provider
chunks. v1 ships the mock provider; a real (OpenAI-compatible) adapter is added
behind the same interface later (open §5).
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Literal, Protocol

StopReason = Literal["stop", "tool_use", "length"]

Message = dict[str, object]
ToolSchema = dict[str, object]


@dataclasses.dataclass(frozen=True)
class TextDelta:
    text: str
    kind: Literal["text-delta"] = "text-delta"


@dataclasses.dataclass(frozen=True)
class ReasoningDelta:
    text: str
    kind: Literal["reasoning-delta"] = "reasoning-delta"


@dataclasses.dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, object]
    kind: Literal["tool-call"] = "tool-call"


@dataclasses.dataclass(frozen=True)
class Finish:
    stop_reason: StopReason
    # Real usage when the provider reports it (OpenAI `stream_options.include_usage`);
    # None for providers that do not (e.g. mock) — the projection then estimates.
    input_tokens: int | None = None
    output_tokens: int | None = None
    kind: Literal["finish"] = "finish"


ProviderEvent = TextDelta | ReasoningDelta | ToolCall | Finish


class Provider(Protocol):
    """A streaming model provider. `stream` yields normalized ProviderEvents."""

    name: str

    def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]: ...
