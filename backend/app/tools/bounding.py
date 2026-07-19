"""Output bounding at the model boundary (docs/05; ~2000 lines / 50 KiB).

Oversized tool output is trimmed to head+tail with a truncation marker so it never
blows the context window. A physical spill-to-file store lands with the workspace
task; for now bounded output carries the original counts + a truncation flag.
"""

from __future__ import annotations

import dataclasses

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
