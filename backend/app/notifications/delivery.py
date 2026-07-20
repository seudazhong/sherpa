"""Firing delivery: turn pending schedule_firings into web/email notifications.

Honors opt-in, per-channel enablement, quiet hours (per user timezone), and a
daily cap. Delivery is idempotent — a settled firing is never re-delivered — so
a reminder is delivered at most once. Failures settle the firing with an honest
`failed` outcome. The web inbox is projected from settled firings.
"""

from __future__ import annotations

import datetime
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Schedule, ScheduleFiring, UserSettings
from app.notifications.email import EmailSender


async def ensure_settings(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> UserSettings:
    """Return the user's settings row, creating defaults on first access."""
    row = await session.get(UserSettings, (tenant_id, user_id))
    if row is not None:
        return row
    row = UserSettings(
        tenant_id=tenant_id,
        user_id=user_id,
        digest_time=datetime.time(8, 0),
        quiet_hours_start=datetime.time(22, 0),
        quiet_hours_end=datetime.time(8, 0),
        event_types=["new_candidate", "due_soon", "overdue", "run_failed"],
        eventual_delivery_kinds=["overdue"],
    )
    session.add(row)
    await session.flush()
    return row


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def in_quiet_hours(settings: UserSettings, now: datetime.datetime) -> bool:
    if not settings.quiet_hours_enabled:
        return False
    local = now.astimezone(_tz(settings.timezone)).time()
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start < end:
        return start <= local < end
    return local >= start or local < end  # wrap past midnight


def _quiet_end_after(settings: UserSettings, now: datetime.datetime) -> datetime.datetime:
    tz = _tz(settings.timezone)
    local = now.astimezone(tz)
    candidate = local.replace(
        hour=settings.quiet_hours_end.hour,
        minute=settings.quiet_hours_end.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local:
        candidate += datetime.timedelta(days=1)
    return candidate.astimezone(datetime.UTC)


async def _delivered_today(
    session: AsyncSession, tenant_id: uuid.UUID, now: datetime.datetime
) -> int:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    val = await session.scalar(
        select(func.count())
        .select_from(ScheduleFiring)
        .where(
            ScheduleFiring.tenant_id == tenant_id,
            ScheduleFiring.delivery_outcome == "delivered",
            ScheduleFiring.settled_at >= day_start,
        )
    )
    return int(val or 0)


def _settle(firing: ScheduleFiring, outcome: str, now: datetime.datetime) -> None:
    firing.status = "settled"
    firing.delivery_outcome = outcome
    firing.settled_at = now
    firing.updated_at = now


async def deliver_firing(
    session: AsyncSession,
    *,
    firing: ScheduleFiring,
    schedule: Schedule,
    settings: UserSettings,
    sender: EmailSender,
    now: datetime.datetime,
) -> str:
    """Deliver one pending firing. Returns an outcome tag. Caller commits."""
    if firing.status == "settled":
        return "noop"

    channel = schedule.delivery_channel
    channel_on = settings.web_enabled if channel == "web" else settings.email_digest_enabled
    if not settings.notifications_enabled or not channel_on:
        _settle(firing, "missed", now)
        return "suppressed"

    if in_quiet_hours(settings, now):
        firing.available_at = _quiet_end_after(settings, now)
        firing.updated_at = now
        return "deferred_quiet_hours"

    if await _delivered_today(session, firing.tenant_id, now) >= settings.daily_cap:
        firing.available_at = now + datetime.timedelta(hours=1)
        firing.updated_at = now
        return "deferred_cap"

    if channel == "digest_email":
        ok = await sender.send(
            to="owner", subject=f"Sherpa: {schedule.name}", body=f"Reminder for {schedule.name}."
        )
        _settle(firing, "delivered" if ok else "failed", now)
        return "delivered" if ok else "failed"

    _settle(firing, "delivered", now)
    return "delivered"


async def deliver_due_firings(
    session: AsyncSession, sender: EmailSender, now: datetime.datetime
) -> dict[str, int]:
    """Deliver all ready pending firings. Caller commits."""
    firings = (
        (
            await session.execute(
                select(ScheduleFiring)
                .where(
                    ScheduleFiring.status == "pending",
                    ScheduleFiring.available_at <= now,
                )
                .order_by(ScheduleFiring.available_at)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for firing in firings:
        schedule = await session.get(Schedule, (firing.tenant_id, firing.schedule_id))
        if schedule is None:
            continue
        settings = await ensure_settings(session, firing.tenant_id, schedule.user_id)
        outcome = await deliver_firing(
            session, firing=firing, schedule=schedule, settings=settings, sender=sender, now=now
        )
        counts[outcome] = counts.get(outcome, 0) + 1
        await session.flush()
    return counts
