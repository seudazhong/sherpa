"""Output bounding + spill at the model boundary (docs/05, api.md §7.2; 2000 lines
/ 50 KiB).

Oversized tool output is trimmed to head+tail with a truncation marker so it never
blows the context window, and the full redacted result is spilled to a per-invocation
file (`TOOL_OUTPUT_ROOT/{invocation_id}.txt`) referenced from the bounded preview.
"""

from __future__ import annotations

import dataclasses
import pathlib
import uuid

MAX_LINES = 2000
MAX_BYTES = 50 * 1024


@dataclasses.dataclass(frozen=True)
class BoundedOutput:
    text: str
    truncated: bool
    original_lines: int
    original_bytes: int


def bound_text(text: str, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES) -> BoundedOutput:
    raw = text.encode("utf-8")
    lines = text.splitlines()
    if len(lines) <= max_lines and len(raw) <= max_bytes:
        return BoundedOutput(text, False, len(lines), len(raw))

    half = max_lines // 2
    head = lines[:half]
    tail = lines[-half:]
    marker = f"\n… [truncated: {len(lines)} lines, {len(raw)} bytes] …\n"
    preview = "\n".join(head) + marker + "\n".join(tail)
    if len(preview.encode("utf-8")) > max_bytes:
        preview = preview.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return BoundedOutput(preview, True, len(lines), len(raw))


def spill_output(root: str, invocation_id: uuid.UUID, full_text: str) -> str:
    """Write the full redacted output to a per-invocation file; return a spill ref.

    Callers pass already-redacted text (never secrets/tokens/hidden prompts).
    """
    path = pathlib.Path(root) / f"{invocation_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(full_text, encoding="utf-8")
    return f"tool-output:{invocation_id}"
