"""Agent core loop."""

from __future__ import annotations

from app.core.compaction import CompactionResult, compact, estimate_size, should_compact
from app.core.loop import SYSTEM_PROMPT, execute_run
from app.core.resume import resume_approval

__all__ = [
    "execute_run",
    "SYSTEM_PROMPT",
    "compact",
    "estimate_size",
    "should_compact",
    "CompactionResult",
    "resume_approval",
]
