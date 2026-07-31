"""Todo capability (ADR-023, docs/11). Shared by REST + agent tools.

Todos are either the accepted result of a Gmail candidate (`source='gmail_candidate'`,
created by the candidate service) or standalone `source='agent'` todos the agent/user
create here. Functions flush but never commit — the adapter owns the transaction.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import Todo as TodoSchema
from app.api.schemas import TodoPage
from app.models import Todo
from app.services.candidates import decode_cursor, encode_cursor, todo_schema
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound, VersionConflict

_STATUSES = {"open", "completed", "cancelled"}
_PRIORITIES = {"low", "medium", "high"}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _load(db: AsyncSession, tenant_id: uuid.UUID, todo_id: uuid.UUID) -> Todo:
    row = await db.get(Todo, (tenant_id, todo_id))
    if row is None:
        raise NotFound("todo not found")
    return row


async def list_todos(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    status_filter: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
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
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Todo.created_at, Todo.id) < (ts, rid))
    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last.created_at, last.id)
        rows = rows[:limit]
    return TodoPage(items=[todo_schema(r) for r in rows], next_cursor=next_cursor)


async def create_todo(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    title: str,
    description: str | None = None,
    due_at: datetime.datetime | None = None,
    priority: str = "medium",
) -> TodoSchema:
    if not title.strip():
        raise Invalid("title required")
    if priority not in _PRIORITIES:
        raise Invalid("invalid priority")
    todo = Todo(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=ctx.user_id,
        source_candidate_id=None,
        source="agent",
        title=title[:500],
        description=description,
        status="open",
        due_at=due_at,
        priority=priority,
    )
    db.add(todo)
    await db.flush()
    return todo_schema(todo)


async def update_todo(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    todo_id: uuid.UUID,
    if_version: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    due_at: datetime.datetime | None = None,
    snoozed_until: datetime.datetime | None = None,
    priority: str | None = None,
) -> TodoSchema:
    row = await _load(db, ctx.tenant_id, todo_id)
    if row.version != if_version:
        raise VersionConflict("stale todo version")
    if status is not None and status not in _STATUSES:
        raise Invalid("invalid status")
    if priority is not None and priority not in _PRIORITIES:
        raise Invalid("invalid priority")
    if title is not None:
        row.title = title
    if description is not None:
        row.description = description
    if due_at is not None:
        row.due_at = due_at
    if priority is not None:
        row.priority = priority
    if status is not None:
        row.status = status
        row.completed_at = _now() if status == "completed" else None
        if status != "open":
            row.snoozed_until = None
    if snoozed_until is not None and row.status == "open":
        row.snoozed_until = snoozed_until
    row.version += 1
    row.updated_at = _now()
    await db.flush()
    return todo_schema(row)
