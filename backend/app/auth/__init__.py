"""Session auth: opaque server-side sessions + CSRF (api.md §2.2, config §2)."""

from __future__ import annotations

from app.auth.deps import (
    RequestContext,
    current_session,
    require_context,
    require_csrf,
)
from app.auth.owner import ensure_owner, owner_ids
from app.auth.store import (
    SessionData,
    cookie_value,
    create_session,
    delete_session,
    load_session,
    refresh_csrf,
)

__all__ = [
    "RequestContext",
    "current_session",
    "require_context",
    "require_csrf",
    "ensure_owner",
    "owner_ids",
    "SessionData",
    "cookie_value",
    "create_session",
    "delete_session",
    "load_session",
    "refresh_csrf",
]
