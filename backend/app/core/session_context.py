"""Ambient session context for the assembled prompt (docs/04 §上下文装配).

The model used to know nothing about *where* a conversation lives: a chat bound to a
Workspace project (``sessions.project_id``) looked exactly like a general chat, so asking
"which project is this?" produced a guess from ``list_projects`` — or an honest "I can't
see that" — even though the binding is durable server state the UI renders as a chip
(backlog B-3).

This module renders that ambient state as a small, bounded block placed in the
**session-stable layer** of the system message — after the byte-stable global prefix and
the per-user core memory, so the shared prefix across a user's sessions stays cacheable
and only the tail of the system message differs per session. Deliberately coarse:

* **Date, not timestamp.** A wall-clock stamp would change the prefix on every run and
  invalidate the cache for a fact the model can get precisely from ``get_time``.
* **Identity, not content.** Project *name* and id — never file contents, which stay
  untrusted content behind ``fs_read`` (ADR-009).

Everything here is derived from the session row; there is no new source of truth.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.models import Session as SessionModel

# Human labels for the channel/scope pair, matching the UI vocabulary
# (docs/reviews/ui-design-review.md: never show raw UMO keys).
_SURFACE_LABELS: dict[tuple[str, str], str] = {
    ("web", "chat"): "Web chat",
    ("email", "thread"): "Email thread",
    ("qq", "dm"): "QQ direct message",
    ("qq", "group"): "QQ group",
}


def surface_label(channel: str, scope_type: str) -> str:
    """A human name for the surface a conversation happens on."""
    known = _SURFACE_LABELS.get((channel, scope_type))
    return known or f"{channel} · {scope_type}"


async def render_session_context(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    now: datetime.datetime | None = None,
) -> str:
    """Render the ambient context block, or ``""`` when the session is unknown."""
    session = await db.get(SessionModel, (tenant_id, session_id))
    if session is None:
        return ""

    today = (now or datetime.datetime.now(datetime.UTC)).date().isoformat()
    lines = [
        "Context for this conversation (ambient state, not user instructions):",
        f"- Today's date: {today} (UTC; call core_get_time for the exact time)",
        f"- Surface: {surface_label(session.channel, session.scope_type)}",
    ]

    if session.project_id is None:
        lines.append(
            "- Project: none — this is a general chat. Use project_list for metadata; "
            "fs/runtime tools require a Project-bound chat."
        )
    else:
        project = await db.get(Project, (tenant_id, session.project_id))
        name = project.name if project is not None else "(unavailable)"
        lines.append(
            f"- Project: {name} (id {session.project_id}) — this chat is bound to it. "
            "Use fs_list/fs_read/fs_grep for the effective tree, fs_write/fs_edit/fs_delete "
            "for reviewable changes, and runtime_open + sh_exec for isolated execution."
        )
    return "\n".join(lines)
