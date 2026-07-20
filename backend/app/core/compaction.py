"""Transcript compaction (docs/04 core-loop — guardrail table).

When the assembled provider window grows past a budget, keep the head (system +
first user turn) and the most recent turns, and replace the omitted middle with a
single summary marker. Two invariants are enforced:

* **verify-shrank** — a compaction that would not actually reduce the window is
  rejected and the original is returned unchanged (never inflate);
* **no orphan tool results** — a ``role:"tool"`` message whose originating
  assistant ``tool_calls`` was dropped is removed, so the provider never sees a
  tool result without its call.

Session identity is unchanged; only the in-flight message window is rewritten.
"""

from __future__ import annotations

import dataclasses

Message = dict[str, object]

_MARKER_ROLE = "system"


def estimate_size(messages: list[Message]) -> int:
    """Cheap, deterministic window-size proxy: chars of content + tool call payloads."""
    total = 0
    for m in messages:
        content = m.get("content")
        if content is not None:
            total += len(str(content))
        tool_calls = m.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                total += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return total


def _drop_orphan_tool_results(messages: list[Message]) -> list[Message]:
    """Drop role:tool messages whose tool_call_id has no preceding assistant call."""
    seen: set[str] = set()
    out: list[Message] = []
    for m in messages:
        if m.get("role") == "tool" and str(m.get("tool_call_id")) not in seen:
            continue  # orphan tool result — its assistant call was compacted away
        out.append(m)
        tool_calls = m.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict) and tc.get("id"):
                    seen.add(str(tc["id"]))
    return out


@dataclasses.dataclass(frozen=True)
class CompactionResult:
    messages: list[Message]
    shrank: bool
    before: int
    after: int
    omitted: int


def should_compact(messages: list[Message], budget: int) -> bool:
    return estimate_size(messages) > budget


def compact(messages: list[Message], *, keep_head: int, keep_recent: int) -> CompactionResult:
    """Keep head + recent, summarize the middle. Rejects any non-shrinking result."""
    before = estimate_size(messages)
    n = len(messages)
    if n <= keep_head + keep_recent:
        return CompactionResult(messages, False, before, before, 0)

    head = messages[:keep_head]
    recent = messages[-keep_recent:] if keep_recent > 0 else []
    omitted = n - keep_head - keep_recent
    marker: Message = {
        "role": _MARKER_ROLE,
        "content": f"[Compacted {omitted} earlier message(s) to fit the context window.]",
    }
    candidate = _drop_orphan_tool_results([*head, marker, *recent])
    after = estimate_size(candidate)
    if after >= before or len(candidate) >= n:
        return CompactionResult(messages, False, before, before, 0)  # would not shrink
    return CompactionResult(candidate, True, before, after, omitted)
