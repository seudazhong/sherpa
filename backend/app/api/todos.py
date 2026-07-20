"""Todo REST endpoints (api.md §4.3). Thin adapter over app.services.todos.

Parse HTTP → CallerContext(actor="user") → service → commit → map ServiceError.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import Todo as TodoSchema
from app.api.schemas import TodoCreate, TodoPage, TodoPatch
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.services import CallerContext, ServiceError
from app.services import todos as svc

router = APIRouter(tags=["todos"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


@router.get("/todos")
async def list_todos(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TodoPage:
    try:
        return await svc.list_todos(
            db, _caller(ctx), status_filter=status_filter, cursor=cursor, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None


@router.post("/todos", status_code=status.HTTP_201_CREATED)
async def create_todo(
    body: TodoCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TodoSchema:
    try:
        todo = await svc.create_todo(
            db,
            _caller(ctx),
            title=body.title,
            description=body.description,
            due_at=body.due_at,
            priority=body.priority,
        )
        await db.commit()
        return todo
    except ServiceError as e:
        raise _http(e) from None


@router.patch("/todos/{todo_id}")
async def patch_todo(
    todo_id: uuid.UUID,
    body: TodoPatch,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TodoSchema:
    try:
        todo = await svc.update_todo(
            db,
            _caller(ctx),
            todo_id=todo_id,
            if_version=body.if_version,
            title=body.title,
            description=body.description,
            status=body.status,
            due_at=body.due_at,
            snoozed_until=body.snoozed_until,
            priority=body.priority,
        )
        await db.commit()
        return todo
    except ServiceError as e:
        raise _http(e) from None
