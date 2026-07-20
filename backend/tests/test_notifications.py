"""Notification delivery (m2-19): delivered once, quiet-hours defers, disabled
suppresses, email failure surfaces. Direct service tests; seed + rollback."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Schedule, ScheduleFiring, Tenant, User
from app.notifications import RecordingEmailSender, deliver_firing, ensure_settings

_UTC = datetime.UTC


class _FailingSender:
    async def send(self, *, to: str, subject: str, body: str) -> bool:
        return False


async def _seed(
    s: AsyncSession, *, channel: str = "web"
) -> tuple[uuid.UUID, ScheduleFiring, Schedule]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    now = datetime.datetime.now(_UTC)
    sched = Schedule(
        tenant_id=tid,
        id=uuid.uuid4(),
        user_id=uid,
        kind="daily_digest",
        name="Morning digest",
        delivery_channel=channel,
        timezone="UTC",
        local_time=datetime.time(8, 0),
        next_fire_at=now,
        misfire_policy="fire_once",
        duplicate_policy="prefer_no_duplicate",
        status="active",
    )
    s.add(sched)
    await s.flush()
    firing = ScheduleFiring(
        tenant_id=tid,
        id=uuid.uuid4(),
        schedule_id=sched.id,
        firing_key=uuid.uuid4().hex,
        scheduled_for=now,
        delivery_idempotency_key=uuid.uuid4().hex,
        status="pending",
        available_at=now,
    )
    s.add(firing)
    await s.flush()
    return tid, firing, sched


@pytest.mark.asyncio
async def test_web_delivered_once_and_idempotent() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, firing, sched = await _seed(s)
            settings = await ensure_settings(s, tid, sched.user_id)
            settings.notifications_enabled = True
            settings.quiet_hours_enabled = False
            await s.flush()
            now = datetime.datetime.now(_UTC)

            first = await deliver_firing(
                s,
                firing=firing,
                schedule=sched,
                settings=settings,
                sender=RecordingEmailSender(),
                now=now,
            )
            assert first == "delivered"
            assert firing.status == "settled" and firing.delivery_outcome == "delivered"

            # delivering a settled firing is a no-op (delivered at most once)
            again = await deliver_firing(
                s,
                firing=firing,
                schedule=sched,
                settings=settings,
                sender=RecordingEmailSender(),
                now=now,
            )
            assert again == "noop"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_quiet_hours_defers() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, firing, sched = await _seed(s)
            settings = await ensure_settings(s, tid, sched.user_id)
            settings.notifications_enabled = True
            settings.quiet_hours_enabled = True
            settings.quiet_hours_start = datetime.time(0, 0, 0)
            settings.quiet_hours_end = datetime.time(23, 59, 59)
            await s.flush()

            outcome = await deliver_firing(
                s,
                firing=firing,
                schedule=sched,
                settings=settings,
                sender=RecordingEmailSender(),
                now=datetime.datetime.now(_UTC),
            )
            assert outcome == "deferred_quiet_hours"
            assert firing.status == "pending" and firing.delivery_outcome is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_disabled_suppresses() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, firing, sched = await _seed(s)
            settings = await ensure_settings(s, tid, sched.user_id)
            settings.notifications_enabled = False
            await s.flush()

            outcome = await deliver_firing(
                s,
                firing=firing,
                schedule=sched,
                settings=settings,
                sender=RecordingEmailSender(),
                now=datetime.datetime.now(_UTC),
            )
            assert outcome == "suppressed"
            assert firing.status == "settled" and firing.delivery_outcome == "missed"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_email_failure_is_honest() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, firing, sched = await _seed(s, channel="digest_email")
            settings = await ensure_settings(s, tid, sched.user_id)
            settings.notifications_enabled = True
            settings.email_digest_enabled = True
            settings.quiet_hours_enabled = False
            await s.flush()

            outcome = await deliver_firing(
                s,
                firing=firing,
                schedule=sched,
                settings=settings,
                sender=_FailingSender(),
                now=datetime.datetime.now(_UTC),
            )
            assert outcome == "failed"
            assert firing.status == "settled" and firing.delivery_outcome == "failed"
        finally:
            await s.rollback()
