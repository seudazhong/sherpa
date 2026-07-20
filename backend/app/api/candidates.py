"""Candidate inbox + todo endpoints (api.md §3.3, §4.3).

Candidates are read-only projections of the analysis pipeline; accept/edit
atomically create exactly one linked todo (bidirectional deferred FKs), dismiss
records the decision. All mutations are optimistic-concurrency guarded by
`if_version`. Tenant-scoped; resources outside the tenant return 404.
"""

from __future__ import annotations

import base64
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
    CandidateSource,
    InferredField,
    TodoPage,
    TodoPatch,
)
from app.api.schemas import (
    Todo as TodoSchema,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import Candidate, ConnectorItem, Extraction, Todo

router = APIRouter(tags=["candidates"])


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _encode(created_at: datetime.datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, rid = raw.split("|", 1)
        return datetime.datetime.fromisoformat(ts), uuid.UUID(rid)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad_cursor") from None


async def _candidate_schema(db: AsyncSession, row: Candidate) -> CandidateSchema:
    ext = await db.get(Extraction, (row.tenant_id, row.extraction_id))
    item = (
        await db.get(ConnectorItem, (row.tenant_id, ext.connector_item_id))
        if ext is not None
        else None
    )
    if item is None:
        raise HTTPException(status_code=500, detail="candidate provenance missing")
    content = item.content_json or {}
    source = CandidateSource(
        kind="gmail",
        connector_id=item.connector_id,
        item_id=item.id,
        revision=item.revision,
        thread_id=item.provider_thread_id or "",
        subject=content.get("subject"),
        sender=content.get("from"),
        received_at=item.received_at,
        excerpt=row.source_excerpt_redacted or content.get("snippet"),
        deep_link=None,
    )
    return CandidateSchema(
        id=row.id,
        tenant_id=row.tenant_id,
        status=row.status,  # type: ignore[arg-type]
        title=row.title,
        description=row.description,
        due_at=row.due_at,
        priority=row.priority,  # type: ignore[arg-type]
        confidence=float(row.confidence),
        inferred_fields=[
            InferredField(
                field="title", confidence=float(row.confidence), evidence=row.rationale_redacted
            )
        ],
        source=source,
        accepted_todo_id=row.accepted_todo_id,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _todo_schema(row: Todo) -> TodoSchema:
    return TodoSchema(
        id=row.id,
        tenant_id=row.tenant_id,
        source_candidate_id=row.source_candidate_id,
        title=row.title,
        description=row.description,
        status=row.status,  # type: ignore[arg-type]
        due_at=row.due_at,
        snoozed_until=row.snoozed_until,
        completed_at=row.completed_at,
        priority=row.priority,  # type: ignore[arg-type]
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _load_candidate(
    db: AsyncSession, tenant_id: uuid.UUID, candidate_id: uuid.UUID
) -> Candidate:
    row = await db.get(Candidate, (tenant_id, candidate_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="candidate not found")
    return row


def _guard(row: Candidate, if_version: int) -> None:
    if row.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_pending")
    if row.version != if_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="version_conflict")


async def _create_todo(
    db: AsyncSession,
    ctx: RequestContext,
    candidate: Candidate,
    *,
    title: str,
    description: str | None,
    due_at: datetime.datetime | None,
    priority: str,
    edited: bool,
) -> Todo:
    todo_id = uuid.uuid4()
    now = _now()
    candidate.status = "edited" if edited else "accepted"
    candidate.accepted_todo_id = todo_id
    candidate.decided_by_user_id = ctx.user_id
    candidate.decided_at = now
    candidate.version += 1
    if edited:
        candidate.title = title
        candidate.description = description
        candidate.due_at = due_at
        candidate.priority = priority
    await db.flush()
    todo = Todo(
        tenant_id=ctx.tenant_id,
        id=todo_id,
        user_id=ctx.user_id,
        source_candidate_id=candidate.id,
        source="gmail_candidate",
        title=title,
        description=description,
        status="open",
        due_at=due_at,
        priority=priority,
    )
    db.add(todo)
    await db.flush()
    await db.commit()
    return todo


@router.get("/candidates")
async def list_candidates(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str, Query(alias="status")] = "pending",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CandidatePage:
    stmt = (
        select(Candidate)
        .where(Candidate.tenant_id == ctx.tenant_id, Candidate.status == status_filter)
        .order_by(Candidate.created_at.desc(), Candidate.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        ts, rid = _decode(cursor)
        stmt = stmt.where(tuple_(Candidate.created_at, Candidate.id) < (ts, rid))
    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode(last.created_at, last.id)
        rows = rows[:limit]
    return CandidatePage(
        items=[await _candidate_schema(db, r) for r in rows], next_cursor=next_cursor
    )


@router.post("/candidates/{candidate_id}/accept", status_code=status.HTTP_201_CREATED)
async def accept_candidate(
    candidate_id: uuid.UUID,
    body: CandidateAccept,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateAcceptance:
    row = await _load_candidate(db, ctx.tenant_id, candidate_id)
    _guard(row, body.if_version)
    todo = await _create_todo(
        db,
        ctx,
        row,
        title=row.title,
        description=row.description,
        due_at=row.due_at,
        priority=row.priority,
        edited=False,
    )
    return CandidateAcceptance(candidate=await _candidate_schema(db, row), todo=_todo_schema(todo))


@router.post("/candidates/{candidate_id}/edit", status_code=status.HTTP_201_CREATED)
async def edit_candidate(
    candidate_id: uuid.UUID,
    body: CandidateEdit,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateAcceptance:
    row = await _load_candidate(db, ctx.tenant_id, candidate_id)
    _guard(row, body.if_version)
    if (
        body.title is None
        and body.description is None
        and body.due_at is None
        and body.priority is None
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="no_edits")
    todo = await _create_todo(
        db,
        ctx,
        row,
        title=body.title or row.title,
        description=body.description if body.description is not None else row.description,
        due_at=body.due_at if body.due_at is not None else row.due_at,
        priority=body.priority or row.priority,
        edited=True,
    )
    return CandidateAcceptance(candidate=await _candidate_schema(db, row), todo=_todo_schema(todo))


@router.post("/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(
    candidate_id: uuid.UUID,
    body: CandidateDismiss,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateSchema:
    row = await _load_candidate(db, ctx.tenant_id, candidate_id)
    _guard(row, body.if_version)
    row.status = "dismissed"
    row.decided_by_user_id = ctx.user_id
    row.decided_at = _now()
    row.version += 1
    await db.commit()
    return await _candidate_schema(db, row)


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
        ts, rid = _decode(cursor)
        stmt = stmt.where(tuple_(Todo.created_at, Todo.id) < (ts, rid))
    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode(last.created_at, last.id)
        rows = rows[:limit]
    return TodoPage(items=[_todo_schema(r) for r in rows], next_cursor=next_cursor)


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
    return _todo_schema(row)
