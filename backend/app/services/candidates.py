"""Candidate capability (ADR-023, docs/11). Extracted from the REST handler so the
REST endpoints and the agent tools share one implementation.

Candidates are read-only projections of the analysis pipeline; accept/edit
atomically create exactly one linked todo (bidirectional deferred FKs), dismiss
records the decision. All mutations are optimistic-concurrency guarded by
`if_version`. Functions flush but never commit — the adapter owns the transaction.
"""

from __future__ import annotations

import base64
import datetime
import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    Candidate as CandidateSchema,
)
from app.api.schemas import (
    CandidateAcceptance,
    CandidatePage,
    CandidateSource,
    InferredField,
)
from app.api.schemas import (
    Todo as TodoSchema,
)
from app.models import Candidate, ConnectorItem, Extraction, Todo
from app.services.context import CallerContext
from app.services.errors import Conflict, Internal, Invalid, NotFound, VersionConflict


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def encode_cursor(created_at: datetime.datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, rid = raw.split("|", 1)
        return datetime.datetime.fromisoformat(ts), uuid.UUID(rid)
    except Exception:
        raise Invalid("bad_cursor") from None


async def candidate_schema(db: AsyncSession, row: Candidate) -> CandidateSchema:
    ext = await db.get(Extraction, (row.tenant_id, row.extraction_id))
    item = (
        await db.get(ConnectorItem, (row.tenant_id, ext.connector_item_id))
        if ext is not None
        else None
    )
    if item is None:
        raise Internal("candidate provenance missing")
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


def todo_schema(row: Todo) -> TodoSchema:
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


async def _load(db: AsyncSession, tenant_id: uuid.UUID, candidate_id: uuid.UUID) -> Candidate:
    row = await db.get(Candidate, (tenant_id, candidate_id))
    if row is None:
        raise NotFound("candidate not found")
    return row


def _guard(row: Candidate, if_version: int) -> None:
    if row.status != "pending":
        raise Conflict("candidate is not pending")
    if row.version != if_version:
        raise VersionConflict("stale candidate version")


async def _create_todo(
    db: AsyncSession,
    ctx: CallerContext,
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
    return todo


async def list_candidates(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    status_filter: str = "pending",
    cursor: str | None = None,
    limit: int = 20,
) -> CandidatePage:
    stmt = (
        select(Candidate)
        .where(Candidate.tenant_id == ctx.tenant_id, Candidate.status == status_filter)
        .order_by(Candidate.created_at.desc(), Candidate.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Candidate.created_at, Candidate.id) < (ts, rid))
    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last.created_at, last.id)
        rows = rows[:limit]
    return CandidatePage(
        items=[await candidate_schema(db, r) for r in rows], next_cursor=next_cursor
    )


async def accept_candidate(
    db: AsyncSession, ctx: CallerContext, *, candidate_id: uuid.UUID, if_version: int
) -> CandidateAcceptance:
    row = await _load(db, ctx.tenant_id, candidate_id)
    _guard(row, if_version)
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
    return CandidateAcceptance(candidate=await candidate_schema(db, row), todo=todo_schema(todo))


async def edit_candidate(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    candidate_id: uuid.UUID,
    if_version: int,
    title: str | None = None,
    description: str | None = None,
    due_at: datetime.datetime | None = None,
    priority: str | None = None,
) -> CandidateAcceptance:
    row = await _load(db, ctx.tenant_id, candidate_id)
    _guard(row, if_version)
    if title is None and description is None and due_at is None and priority is None:
        raise Invalid("no_edits")
    todo = await _create_todo(
        db,
        ctx,
        row,
        title=title or row.title,
        description=description if description is not None else row.description,
        due_at=due_at if due_at is not None else row.due_at,
        priority=priority or row.priority,
        edited=True,
    )
    return CandidateAcceptance(candidate=await candidate_schema(db, row), todo=todo_schema(todo))


async def dismiss_candidate(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    candidate_id: uuid.UUID,
    if_version: int,
    reason: str | None = None,
) -> CandidateSchema:
    row = await _load(db, ctx.tenant_id, candidate_id)
    _guard(row, if_version)
    row.status = "dismissed"
    row.decided_by_user_id = ctx.user_id
    row.decided_at = _now()
    row.version += 1
    await db.flush()
    return await candidate_schema(db, row)
