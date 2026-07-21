"""User core-memory REST (ADR-023: parity with the memory_user_* agent tools).

Thin adapter over app.services.memory so the human client (a Memory page) and the
agent tools share one capability layer. Own-data; reads need a session, writes
also need CSRF.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.services import CallerContext, ServiceError
from app.services import memory as svc

router = APIRouter(tags=["memory"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class MemoryItem(BaseModel):
    key: str
    value: str
    version: int


class MemoryPage(BaseModel):
    items: list[MemoryItem]


class MemorySet(BaseModel):
    key: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(min_length=1, max_length=16384)]


@router.get("/memory")
async def list_memory(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MemoryPage:
    rows = await svc.list_memory(db, _caller(ctx))
    return MemoryPage(
        items=[MemoryItem(key=r.memory_key, value=r.value_text, version=r.version) for r in rows]
    )


@router.put("/memory")
async def set_memory(
    body: MemorySet,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MemoryItem:
    try:
        row = await svc.set_memory(db, _caller(ctx), key=body.key, value=body.value)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return MemoryItem(key=row.memory_key, value=row.value_text, version=row.version)


@router.delete("/memory/{key}", status_code=204)
async def delete_memory(
    key: str,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    try:
        await svc.delete_memory(db, _caller(ctx), key=key)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
