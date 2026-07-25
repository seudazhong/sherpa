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


class ProviderError(Exception):
    """A provider call failed at the HTTP/transport layer (not a model refusal).

    Carries the HTTP status and a bounded, redacted response body so the failure
    is diagnosable from logs + the run journal without a re-run — the exact gap
    behind "400 bad request, logs too terse". The body is provider error text
    (e.g. a proxy's JSON error such as {"error":{"message":"No connected db"}});
    it is bounded and passed through secret redaction before being surfaced.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, body: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def detail(self) -> str:
        """A compact one-line description for logs + the run.settled reason."""
        parts = [str(self)]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.body:
            parts.append(f"body={self.body}")
        return " ".join(parts)


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
