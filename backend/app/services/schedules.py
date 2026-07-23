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
from app.config import settings
from app.models import Schedule, ScheduleFiring, Todo
from app.scheduler.cadence import CadenceError, first_fire_at, validate_cadence
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
        cadence_kind=row.cadence_kind,  # type: ignore[arg-type]
        cron_expr=row.cron_expr,
        interval_seconds=row.interval_seconds,
        weekly_days=row.weekly_days,
        monthly_day=row.monthly_day,
        prompt=row.prompt,
        next_fire_at=row.next_fire_at,
        last_fired_at=row.last_fired_at,
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
    cadence_kind: str | None = None,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    weekly_days: str | None = None,
    monthly_day: int | None = None,
    prompt: str | None = None,
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
        cron_expr = interval_seconds = weekly_days = monthly_day = prompt = None
    elif kind == "daily_digest":
        if local_time is None:
            raise Invalid("daily_digest needs local_time")
        todo_id, reminder_kind = None, None
        fire_at = _next_daily(local_time, timezone)
        cadence_kind = "daily"
        cron_expr = interval_seconds = weekly_days = monthly_day = prompt = None
    elif kind == "agent_task":
        if not prompt or not prompt.strip():
            raise Invalid("agent_task needs a prompt")
        if len(prompt) > 8000:
            raise Invalid("prompt too long")
        cadence_kind = cadence_kind or "daily"
        try:
            validate_cadence(
                cadence_kind,
                cron_expr=cron_expr,
                interval_seconds=interval_seconds,
                weekly_days=weekly_days,
                monthly_day=monthly_day,
                local_time=local_time,
                min_interval_seconds=settings.scheduled_task_min_interval_seconds,
            )
            fire_at = first_fire_at(
                cadence_kind=cadence_kind,
                now=_now(),
                timezone=timezone,
                local_time=local_time,
                cron_expr=cron_expr,
                interval_seconds=interval_seconds,
                weekly_days=weekly_days,
                monthly_day=monthly_day,
                once_at=next_fire_at,
            )
        except CadenceError as e:
            raise Invalid(str(e)) from None
        todo_id, reminder_kind = None, None
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
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
        weekly_days=weekly_days,
        monthly_day=monthly_day,
        prompt=prompt,
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


async def set_status(
    db: AsyncSession, ctx: CallerContext, *, schedule_id: uuid.UUID, if_version: int, status: str
) -> ScheduleSchema:
    """Pause (active -> paused) or resume (paused -> active) a schedule."""
    if status not in ("active", "paused"):
        raise Invalid("status must be active or paused")
    row = await db.get(Schedule, (ctx.tenant_id, schedule_id))
    if row is None or row.user_id != ctx.user_id:
        raise NotFound("schedule not found")
    if row.version != if_version:
        raise VersionConflict("stale schedule version")
    if row.status in ("disabled", "completed"):
        raise Conflict("schedule already inactive")
    row.status = status
    row.version += 1
    row.updated_at = _now()
    await db.flush()
    return schedule_schema(row)


async def run_now(
    db: AsyncSession, ctx: CallerContext, *, schedule_id: uuid.UUID
) -> ScheduleFiring:
    """Insert an immediate firing for a schedule without advancing its cursor.

    The firing is picked up by the delivery tick (reminder/digest) or the agent-task
    tick (agent_task) on the next pass, exactly like a scheduled slot.
    """
    row = await db.get(Schedule, (ctx.tenant_id, schedule_id))
    if row is None or row.user_id != ctx.user_id:
        raise NotFound("schedule not found")
    if row.status in ("disabled", "completed"):
        raise Conflict("schedule is inactive")
    now = _now()
    # Microsecond-precise slot so a manual run never collides with a scheduled slot.
    key = f"{schedule_id}:manual:{int(now.timestamp() * 1_000_000)}"
    firing = ScheduleFiring(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        schedule_id=schedule_id,
        firing_key=key,
        scheduled_for=now,
        status="pending",
        delivery_idempotency_key=f"firing:{key}",
        available_at=now,
    )
    db.add(firing)
    await db.flush()
    return firing


async def list_firings(
    db: AsyncSession, ctx: CallerContext, *, schedule_id: uuid.UUID, limit: int = 50
) -> list[ScheduleFiring]:
    row = await db.get(Schedule, (ctx.tenant_id, schedule_id))
    if row is None or row.user_id != ctx.user_id:
        raise NotFound("schedule not found")
    rows = (
        (
            await db.execute(
                select(ScheduleFiring)
                .where(
                    ScheduleFiring.tenant_id == ctx.tenant_id,
                    ScheduleFiring.schedule_id == schedule_id,
                )
                .order_by(ScheduleFiring.scheduled_for.desc())
                .limit(max(1, min(limit, 100)))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
