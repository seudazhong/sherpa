"""Auth endpoints (api.md §3.1, §4.1): login / session / logout.

v1 authenticates the single configured owner (constant-time credential compare),
seeds the owner tenant/user, and issues an opaque server-side session cookie plus
a CSRF token. Login rate limiting is deferred (single-user); documented in api.md.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AuthSession, LoginRequest
from app.auth import (
    RequestContext,
    cookie_value,
    create_session,
    current_session,
    delete_session,
    ensure_owner,
    refresh_csrf,
    require_csrf,
)
from app.auth.store import SessionData
from app.config import settings
from app.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, data: SessionData) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value(data),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def _valid_owner(email: str, password: str) -> bool:
    return hmac.compare_digest(email, settings.owner_email) and hmac.compare_digest(
        password, settings.owner_password
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AuthSession:
    if not _valid_owner(body.email, body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    tenant_id, user_id = await ensure_owner(db)
    await db.commit()
    data = await create_session(user_id=user_id, tenant_id=tenant_id, email=settings.owner_email)
    _set_cookie(response, data)
    return AuthSession(
        user_id=data.user_id,
        tenant_id=data.tenant_id,
        email=data.email,
        csrf_token=data.csrf_token,
        expires_at=data.expires_at,
    )


@router.get("/session")
async def session_info(data: Annotated[SessionData, Depends(current_session)]) -> AuthSession:
    rotated = await refresh_csrf(data)
    return AuthSession(
        user_id=rotated.user_id,
        tenant_id=rotated.tenant_id,
        email=rotated.email,
        csrf_token=rotated.csrf_token,
        expires_at=rotated.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    _ctx: Annotated[RequestContext, Depends(require_csrf)],
    data: Annotated[SessionData, Depends(current_session)],
) -> Response:
    await delete_session(cookie_value(data))
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
