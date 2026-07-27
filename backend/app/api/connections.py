"""GitHub connection REST surface (api.md §10.6; ADR-038 W2b).

Thin adapter over ``app.services.github_source`` for the owner's GitHub credential
(a fine-grained PAT with ``contents:read``, first version). The token is AEAD-sealed
server-side on receipt and **never** returned to the client — this surface only ever
exposes status (id + display login + scopes + status). GitHub import is **human-only**;
there is **no** agent tool. Reads need a session; writes also need CSRF.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.services import CallerContext, ServiceError
from app.services import github_source as gh

logger = logging.getLogger("app.api.connections")
router = APIRouter(tags=["connections"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class GithubConnectionStatus(BaseModel):
    id: str | None
    connected: bool
    auth_kind: Literal["pat", "app_installation"] | None
    account_login: str | None
    scopes: list[str]
    status: Literal["pending", "active", "revoked", "error"] | None
    last_error_redacted: str | None


class GithubConnectionCreate(BaseModel):
    auth_kind: Literal["pat"] = "pat"
    token: Annotated[SecretStr, Field(min_length=1)]


def _status_out(s: gh.ConnectionStatus) -> GithubConnectionStatus:
    return GithubConnectionStatus(
        id=str(s.id) if s.id is not None else None,
        connected=s.connected,
        auth_kind=s.auth_kind,  # type: ignore[arg-type]
        account_login=s.account_login,
        scopes=s.scopes,
        status=s.status,  # type: ignore[arg-type]
        last_error_redacted=s.last_error_redacted,
    )


@router.get("/connections/github")
async def get_github_connection(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> GithubConnectionStatus:
    try:
        s = await gh.get_status(db, _caller(ctx))
    except ServiceError as e:
        raise _http(e) from None
    return _status_out(s)


@router.post("/connections/github", status_code=status.HTTP_201_CREATED)
async def create_github_connection(
    body: GithubConnectionCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> GithubConnectionStatus:
    try:
        await gh.create_connection(
            db,
            _caller(ctx),
            auth_kind=body.auth_kind,
            token=body.token.get_secret_value(),
        )
        s = await gh.get_status(db, _caller(ctx))
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return _status_out(s)


@router.delete("/connections/github", status_code=status.HTTP_204_NO_CONTENT)
async def delete_github_connection(
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        await gh.delete_connection(db, _caller(ctx))
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
