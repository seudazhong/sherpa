"""Schedule capability (ADR-023, docs/11; api.md §4.4). Shared by REST + tools.

v1 schedules are reminders/digests only. A `todo_reminder` fires once at an
absolute `next_fire_at` for a specific todo; a `daily_digest` fires daily at a
local time (next_fire_at is computed from timezone + local_time). Functions flush
but never commit — the adapter owns the transaction.
"""

from __future__ import annotations

import datetime
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import Schedule as ScheduleSchema
from app.api.schemas import SchedulePage
from app.models import Schedule, Todo
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound, VersionConflict


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def schedule_schema(row: Schedule) -> ScheduleSchema:
    return ScheduleSchema(
        id=row.id,
        tenant_id=row.tenant_id,
        kind=row.kind,  # type: ignore[arg-type]
        name=row.name,
        todo_id=row.todo_id,
        reminder_kind=row.reminder_kind,  # type: ignore[arg-type]
        delivery_channel=row.delivery_channel,  # type: ignore[arg-type]
        timezone=row.timezone,
        local_time=row.local_time,
        next_fire_at=row.next_fire_at,
        status=row.status,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Invalid(f"invalid timezone: {name}") from exc


def _next_daily(local_time: datetime.time, tz_name: str) -> datetime.datetime:
    """Next occurrence of local_time in tz, as an aware UTC datetime."""
    tz = _tz(tz_name)
    local_now = _now().astimezone(tz)
    candidate = local_now.replace(
        hour=local_time.hour, minute=local_time.minute, second=0, microsecond=0
    )
    if candidate <= local_now:
        candidate += datetime.timedelta(days=1)
    return candidate.astimezone(datetime.UTC)


async def create_schedule(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    kind: str,
    name: str,
    delivery_channel: str = "web",
    timezone: str = "UTC",
    local_time: datetime.time | None = None,
    todo_id: uuid.UUID | None = None,
    reminder_kind: str | None = None,
    next_fire_at: datetime.datetime | None = None,
) -> ScheduleSchema:
    _tz(timezone)  # validate
    if kind == "todo_reminder":
        if todo_id is None or reminder_kind is None:
            raise Invalid("todo_reminder needs todo_id + reminder_kind")
        if next_fire_at is None:
            raise Invalid("todo_reminder needs next_fire_at")
        if await db.get(Todo, (ctx.tenant_id, todo_id)) is None:
            raise NotFound("todo not found")
        local_time = None
        fire_at = next_fire_at
        cadence_kind = "once"
    elif kind == "daily_digest":
        if local_time is None:
            raise Invalid("daily_digest needs local_time")
        todo_id, reminder_kind = None, None
        fire_at = _next_daily(local_time, timezone)
        cadence_kind = "daily"
    else:
        raise Invalid("invalid kind")
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=datetime.UTC)
    if fire_at <= _now():
        raise Invalid("next_fire_at must be in the future")

    row = Schedule(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=ctx.user_id,
        todo_id=todo_id,
        kind=kind,
        name=name,
        reminder_kind=reminder_kind,
        delivery_channel=delivery_channel,
        timezone=timezone,
        local_time=local_time,
        cadence_kind=cadence_kind,
        next_fire_at=fire_at,
        misfire_policy="fire_once",
        duplicate_policy="prefer_no_duplicate",
        status="active",
    )
    db.add(row)
    await db.flush()
    return schedule_schema(row)


async def list_schedules(db: AsyncSession, ctx: CallerContext) -> SchedulePage:
    rows = (
        (
            await db.execute(
                select(Schedule)
                .where(Schedule.tenant_id == ctx.tenant_id)
                .order_by(Schedule.next_fire_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return SchedulePage(items=[schedule_schema(r) for r in rows], next_cursor=None)


async def cancel_schedule(
    db: AsyncSession, ctx: CallerContext, *, schedule_id: uuid.UUID, if_version: int
) -> ScheduleSchema:
    row = await db.get(Schedule, (ctx.tenant_id, schedule_id))
    if row is None:
        raise NotFound("schedule not found")
    if row.version != if_version:
        raise VersionConflict("stale schedule version")
    if row.status in ("disabled", "completed"):
        raise Conflict("schedule already inactive")
    row.status = "disabled"
    row.version += 1
    row.updated_at = _now()
    await db.flush()
    return schedule_schema(row)
