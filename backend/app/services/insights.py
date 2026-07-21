"""Read + settings capability (ADR-023, docs/11): notifications, activity, settings.

Shared by REST + agent tools. Reads are tenant-scoped projections; settings update
is optimistic-concurrency guarded. Functions flush but never commit — the adapter
owns the transaction.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ActivityPage,
    ActivityReceipt,
    Notification,
    NotificationPage,
    Settings,
)
from app.models import AuditReceipt, Schedule, ScheduleFiring, UserSettings
from app.notifications import ensure_settings
from app.services.context import CallerContext
from app.services.errors import Invalid, VersionConflict


def _settings_schema(row: UserSettings) -> Settings:
    return Settings(
        notifications_enabled=row.notifications_enabled,
        web_enabled=row.web_enabled,
        email_digest_enabled=row.email_digest_enabled,
        timezone=row.timezone,
        quiet_hours_enabled=row.quiet_hours_enabled,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        daily_cap=row.daily_cap,
        version=row.version,
    )


def _receipt_schema(row: AuditReceipt) -> ActivityReceipt:
    return ActivityReceipt(
        id=row.id,
        receipt_type=row.receipt_type,
        actor_type=row.actor_type,
        trigger_type=row.trigger_type,
        action=row.action,
        outcome=row.outcome,
        reversible=row.reversible,
        summary=row.summary_redacted,
        run_id=row.run_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        occurred_at=row.occurred_at,
    )


async def list_notifications(
    db: AsyncSession, ctx: CallerContext, *, limit: int = 50
) -> NotificationPage:
    rows = (
        await db.execute(
            select(ScheduleFiring, Schedule)
            .join(
                Schedule,
                (Schedule.tenant_id == ScheduleFiring.tenant_id)
                & (Schedule.id == ScheduleFiring.schedule_id),
            )
            .where(
                ScheduleFiring.tenant_id == ctx.tenant_id,
                ScheduleFiring.status == "settled",
            )
            .order_by(ScheduleFiring.scheduled_for.desc())
            .limit(limit)
        )
    ).all()
    items = [
        Notification(
            firing_id=firing.id,
            schedule_id=schedule.id,
            schedule_name=schedule.name,
            channel=schedule.delivery_channel,
            scheduled_for=firing.scheduled_for,
            status=firing.status,
            delivery_outcome=firing.delivery_outcome,
            settled_at=firing.settled_at,
        )
        for firing, schedule in rows
    ]
    return NotificationPage(items=items, next_cursor=None)


async def list_activity(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    receipt_type: str | None = None,
    cursor_ts_id: tuple[object, object] | None = None,
    limit: int = 30,
) -> ActivityPage:
    stmt = (
        select(AuditReceipt)
        .where(AuditReceipt.tenant_id == ctx.tenant_id)
        .order_by(AuditReceipt.occurred_at.desc(), AuditReceipt.id.desc())
        .limit(limit + 1)
    )
    if receipt_type:
        stmt = stmt.where(AuditReceipt.receipt_type == receipt_type)
    if cursor_ts_id:
        stmt = stmt.where(tuple_(AuditReceipt.occurred_at, AuditReceipt.id) < cursor_ts_id)
    rows = (await db.execute(stmt)).scalars().all()
    rows = rows[:limit]
    return ActivityPage(items=[_receipt_schema(r) for r in rows], next_cursor=None)


async def get_settings(db: AsyncSession, ctx: CallerContext) -> Settings:
    row = await ensure_settings(db, ctx.tenant_id, ctx.user_id)
    return _settings_schema(row)


async def update_settings(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    if_version: int,
    notifications_enabled: bool | None = None,
    web_enabled: bool | None = None,
    email_digest_enabled: bool | None = None,
    timezone: str | None = None,
    quiet_hours_enabled: bool | None = None,
    quiet_hours_start: datetime.time | None = None,
    quiet_hours_end: datetime.time | None = None,
    daily_cap: int | None = None,
) -> Settings:
    row = await ensure_settings(db, ctx.tenant_id, ctx.user_id)
    if row.version != if_version:
        raise VersionConflict("stale settings version")
    if notifications_enabled is not None:
        row.notifications_enabled = notifications_enabled
    if web_enabled is not None:
        row.web_enabled = web_enabled
    if email_digest_enabled is not None:
        row.email_digest_enabled = email_digest_enabled
    if timezone is not None:
        row.timezone = timezone
    if quiet_hours_enabled is not None:
        row.quiet_hours_enabled = quiet_hours_enabled
    if quiet_hours_start is not None:
        row.quiet_hours_start = quiet_hours_start
    if quiet_hours_end is not None:
        row.quiet_hours_end = quiet_hours_end
    if daily_cap is not None:
        row.daily_cap = daily_cap
    if row.quiet_hours_start == row.quiet_hours_end:
        raise Invalid("quiet hours start and end must differ")
    row.version += 1
    await db.flush()
    return _settings_schema(row)
