"""Scheduled agent-task dispatch (ADR-031, Phase CRON.2).

Proves a due `agent_task` firing becomes exactly one autonomous run (idempotent on
replay), links `run_id`, respects the per-user concurrency cap, and that the
notification delivery path skips agent_task firings. Integration test — skips
without Postgres; rolls back.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal, ping_db
from app.models import Run, Schedule, ScheduleFiring, Tenant, User
from app.models import Session as SessionModel
from app.notifications import build_email_sender, deliver_due_firings
from app.scheduler import dispatch_due_agent_tasks, fire_due_schedules

_UTC = datetime.UTC


async def _seed(s) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return tid, uid


def _agent_task(tid, uid, when):  # type: ignore[no-untyped-def]
    return Schedule(
        tenant_id=tid,
        id=uuid.uuid4(),
        user_id=uid,
        kind="agent_task",
        name="Morning triage",
        delivery_channel="web",
        timezone="UTC",
        cadence_kind="cron",
        cron_expr="0 9 * * 1-5",
        prompt="Summarize my unread email and list what needs a reply.",
        next_fire_at=when,
        misfire_policy="fire_once",
        duplicate_policy="prefer_no_duplicate",
        status="active",
    )


@pytest.mark.asyncio
async def test_agent_task_fires_and_dispatches_one_run() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            now = datetime.datetime.now(_UTC)
            sched = _agent_task(tid, uid, now - datetime.timedelta(minutes=1))
            s.add(sched)
            await s.flush()

            created = await fire_due_schedules(s, now)
            assert len(created) == 1  # one pending firing

            run_ids = await dispatch_due_agent_tasks(s, now)
            assert len(run_ids) == 1

            firing = await s.get(ScheduleFiring, (tid, created[0]))
            assert firing is not None
            assert firing.run_id == run_ids[0]
            assert firing.status == "running"

            run = await s.get(Run, (tid, run_ids[0]))
            assert run is not None and run.run_kind == "scheduled_task" and run.status == "queued"

            # A dedicated per-schedule session was created and seeded with the prompt.
            sess_id = await s.scalar(
                select(SessionModel.id).where(
                    SessionModel.tenant_id == tid, SessionModel.umo_key == f"scheduled:{sched.id}"
                )
            )
            assert sess_id is not None

            # Replay: the firing is no longer pending → no second run.
            again = await dispatch_due_agent_tasks(s, now)
            assert again == []
            run_count = await s.scalar(
                select(func.count()).select_from(Run).where(Run.tenant_id == tid)
            )
            assert run_count == 1
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_concurrency_cap_defers_dispatch() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            now = datetime.datetime.now(_UTC)
            sched = _agent_task(tid, uid, now - datetime.timedelta(minutes=1))
            s.add(sched)
            await s.flush()
            created = await fire_due_schedules(s, now)

            original = settings.scheduled_task_max_concurrency
            settings.scheduled_task_max_concurrency = 0
            try:
                run_ids = await dispatch_due_agent_tasks(s, now)
            finally:
                settings.scheduled_task_max_concurrency = original

            assert run_ids == []  # capped → deferred, not dropped
            firing = await s.get(ScheduleFiring, (tid, created[0]))
            assert firing is not None
            assert firing.status == "pending"  # still pending
            assert firing.available_at > now  # deferred for a later tick
            assert firing.run_id is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_delivery_skips_agent_task_firings() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            now = datetime.datetime.now(_UTC)
            sched = _agent_task(tid, uid, now - datetime.timedelta(minutes=1))
            s.add(sched)
            await s.flush()
            created = await fire_due_schedules(s, now)

            counts = await deliver_due_firings(s, build_email_sender(), now)
            assert "delivered" not in counts  # agent_task not delivered as a notification

            firing = await s.get(ScheduleFiring, (tid, created[0]))
            assert firing is not None and firing.status == "pending"  # untouched by delivery
        finally:
            await s.rollback()
