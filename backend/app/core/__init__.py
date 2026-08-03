"""Agent core loop."""

from __future__ import annotations

from app.core.admission import Admission, PromptConflict, admit_prompt
from app.core.compaction import CompactionResult, compact, estimate_size, should_compact
from app.core.loop import SYSTEM_PROMPT, execute_run
from app.core.resume import recover_stale_approval_resumes, resume_approval

__all__ = [
    "execute_run",
    "SYSTEM_PROMPT",
    "compact",
    "estimate_size",
    "should_compact",
    "CompactionResult",
    "resume_approval",
    "recover_stale_approval_resumes",
    "Admission",
    "admit_prompt",
    "PromptConflict",
]
