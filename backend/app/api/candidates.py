"""Candidate inbox + todo REST endpoints (api.md §3.3, §4.3).

Thin adapter over `app.services.candidates` (ADR-023): parse HTTP → build a
`CallerContext(actor="user")` → call the shared capability layer → commit → map
`ServiceError` to an HTTP status. The todo list/patch endpoints stay here until the
todo service lands (m-tools T4).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    Candidate as CandidateSchema,
)
from app.api.schemas import (
    CandidateAccept,
    CandidateAcceptance,
    CandidateDismiss,
    CandidateEdit,
    CandidatePage,
    TodoPage,
    TodoPatch,
)
from app.api.schemas import (
    Todo as TodoSchema,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import Todo
from app.services import CallerContext, ServiceError
from app.services import candidates as svc
from app.services.candidates import decode_cursor, encode_cursor, todo_schema

router = APIRouter(tags=["candidates"])


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


@router.get("/candidates")
async def list_candidates(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str, Query(alias="status")] = "pending",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CandidatePage:
    try:
        return await svc.list_candidates(
            db, _caller(ctx), status_filter=status_filter, cursor=cursor, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None


@router.post("/candidates/{candidate_id}/accept", status_code=status.HTTP_201_CREATED)
async def accept_candidate(
    candidate_id: uuid.UUID,
    body: CandidateAccept,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateAcceptance:
    try:
        result = await svc.accept_candidate(
            db, _caller(ctx), candidate_id=candidate_id, if_version=body.if_version
        )
        await db.commit()
        return result
    except ServiceError as e:
        raise _http(e) from None


@router.post("/candidates/{candidate_id}/edit", status_code=status.HTTP_201_CREATED)
async def edit_candidate(
    candidate_id: uuid.UUID,
    body: CandidateEdit,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateAcceptance:
    try:
        result = await svc.edit_candidate(
            db,
            _caller(ctx),
            candidate_id=candidate_id,
            if_version=body.if_version,
            title=body.title,
            description=body.description,
            due_at=body.due_at,
            priority=body.priority,
        )
        await db.commit()
        return result
    except ServiceError as e:
        raise _http(e) from None


@router.post("/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(
    candidate_id: uuid.UUID,
    body: CandidateDismiss,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateSchema:
    try:
        result = await svc.dismiss_candidate(
            db,
            _caller(ctx),
            candidate_id=candidate_id,
            if_version=body.if_version,
            reason=body.reason,
        )
        await db.commit()
        return result
    except ServiceError as e:
        raise _http(e) from None


@router.get("/todos")
async def list_todos(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TodoPage:
    stmt = (
        select(Todo)
        .where(Todo.tenant_id == ctx.tenant_id)
        .order_by(Todo.created_at.desc(), Todo.id.desc())
        .limit(limit + 1)
    )
    if status_filter:
        stmt = stmt.where(Todo.status == status_filter)
    if cursor:
        try:
            ts, rid = decode_cursor(cursor)
        except ServiceError as e:
            raise _http(e) from None
        stmt = stmt.where(tuple_(Todo.created_at, Todo.id) < (ts, rid))
    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last.created_at, last.id)
        rows = rows[:limit]
    return TodoPage(items=[todo_schema(r) for r in rows], next_cursor=next_cursor)


@router.patch("/todos/{todo_id}")
async def patch_todo(
    todo_id: uuid.UUID,
    body: TodoPatch,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TodoSchema:
    row = await db.get(Todo, (ctx.tenant_id, todo_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="todo not found")
    if row.version != body.if_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="version_conflict")
    if body.title is not None:
        row.title = body.title
    if body.description is not None:
        row.description = body.description
    if body.due_at is not None:
        row.due_at = body.due_at
    if body.priority is not None:
        row.priority = body.priority
    if body.status is not None:
        row.status = body.status
        row.completed_at = _now() if body.status == "completed" else None
        if body.status != "open":
            row.snoozed_until = None
    if body.snoozed_until is not None and row.status == "open":
        row.snoozed_until = body.snoozed_until
    row.version += 1
    row.updated_at = _now()
    await db.commit()
    return todo_schema(row)
