"""Pre-authorization grants REST (api.md §4.7; ADR-034).

Owner-only CRUD over app.services.grants. Grants let the loop auto-allow a matching
external action instead of asking. There is intentionally **no agent tool and no
agent-writable path** — the agent must never grant itself permissions. Mutations
require CSRF; `DELETE` soft-revokes.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import PermissionGrant
from app.services import CallerContext, ServiceError
from app.services import grants as svc

router = APIRouter(tags=["grants"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class Grant(BaseModel):
    id: uuid.UUID
    tool_name: str
    match_json: dict
    created_via: str
    created_at: datetime.datetime


class GrantPage(BaseModel):
    items: list[Grant]


class GrantCreate(BaseModel):
    tool_name: str
    match_json: dict


def _grant(row: PermissionGrant) -> Grant:
    return Grant(
        id=row.id,
        tool_name=row.tool_name,
        match_json=row.match_json,
        created_via=row.created_via,
        created_at=row.created_at,
    )


@router.get("/grants")
async def list_grants(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> GrantPage:
    try:
        rows = await svc.list_grants(db, _caller(ctx))
    except ServiceError as e:
        raise _http(e) from None
    return GrantPage(items=[_grant(r) for r in rows])


@router.post("/grants", status_code=201)
async def create_grant(
    body: GrantCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Grant:
    try:
        row = await svc.create_grant(
            db, _caller(ctx), tool_name=body.tool_name, match_json=body.match_json
        )
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _grant(row)


@router.delete("/grants/{grant_id}", status_code=204)
async def delete_grant(
    grant_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    try:
        await svc.revoke_grant(db, _caller(ctx), grant_id=grant_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
