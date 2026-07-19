"""FastAPI auth dependencies: cookie session + CSRF (api.md §2.2, §2.3)."""

from __future__ import annotations

import dataclasses
import hmac
import uuid

from fastapi import HTTPException, Request, status

from app.auth.store import SessionData, load_session
from app.config import settings


@dataclasses.dataclass(frozen=True)
class RequestContext:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    csrf_token: str


def _context(data: SessionData) -> RequestContext:
    return RequestContext(
        user_id=data.user_id,
        tenant_id=data.tenant_id,
        email=data.email,
        csrf_token=data.csrf_token,
    )


async def current_session(request: Request) -> SessionData:
    """Resolve the authenticated session from the cookie, or 401."""
    data = await load_session(request.cookies.get(settings.session_cookie_name))
    if data is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated")
    return data


async def require_context(request: Request) -> RequestContext:
    """Auth-only dependency for safe (GET) requests."""
    return _context(await current_session(request))


async def require_csrf(request: Request) -> RequestContext:
    """Auth + CSRF dependency for unsafe (POST/PATCH/DELETE) requests."""
    data = await current_session(request)
    header = request.headers.get("x-csrf-token")
    if not header or not hmac.compare_digest(header, data.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_failed")
    return _context(data)
