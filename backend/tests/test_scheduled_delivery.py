"""Scheduled agent-task result delivery (ADR-031, Phase CRON.3).

Drives a full scheduled_task run with the mock provider, then settles its firing and
verifies the output is delivered (firing settled `delivered`, visible via the inbox
projection). Integration test — skips without Postgres; rolls back.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import Run, Schedule, ScheduleFiring, Tenant, User
from app.observability import project_run_trace
from app.providers import Finish, MockProvider, TextDelta
from app.scheduler import dispatch_due_agent_tasks, fire_due_schedules
from app.tools import build_default_registry
from app.worker import settle_scheduled_firing

_UTC = datetime.UTC


async def _seed(s) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return tid, uid


def _agent_task(tid, uid, when, channel="web"):  # type: ignore[no-untyped-def]
    return Schedule(
        tenant_id=tid,
        id=uuid.uuid4(),
        user_id=uid,
        kind="agent_task",
        name="Morning triage",
        delivery_channel=channel,
        timezone="UTC",
        cadence_kind="cron",
        cron_expr="0 9 * * 1-5",
        prompt="Summarize my unread email.",
        next_fire_at=when,
        misfire_policy="fire_once",
        duplicate_policy="prefer_no_duplicate",
        status="active",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["web", "email"])
async def test_scheduled_task_run_delivers_and_settles(channel: str) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            now = datetime.datetime.now(_UTC)
            sched = _agent_task(tid, uid, now - datetime.timedelta(minutes=1), channel=channel)
            s.add(sched)
            await s.flush()

            created = await fire_due_schedules(s, now)
            run_ids = await dispatch_due_agent_tasks(s, now)
            assert len(run_ids) == 1
            run = await s.get(Run, (tid, run_ids[0]))
            assert run is not None

            # Execute the scheduled run with a deterministic mock reply.
            provider = MockProvider(script=[[TextDelta("You have 3 unread."), Finish("stop")]])
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"

            outcome = await settle_scheduled_firing(s, run_ids[0])
            assert outcome == "delivered"

            firing = await s.get(ScheduleFiring, (tid, created[0]))
            assert firing is not None
            assert firing.status == "settled"
            assert firing.delivery_outcome == "delivered"
            assert firing.settled_at is not None

            # The settled firing is projected into the web inbox.
            visible = await s.scalar(
                select(ScheduleFiring.id).where(
                    ScheduleFiring.tenant_id == tid,
                    ScheduleFiring.status == "settled",
                    ScheduleFiring.id == created[0],
                )
            )
            assert visible == created[0]
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_scheduled_task_trace_projects() -> None:
    # Regression: ck_traces_kind must admit 'scheduled_task' (else the trace insert
    # on commit rolls back the whole run and marks it failed).
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            now = datetime.datetime.now(_UTC)
            sched = _agent_task(tid, uid, now - datetime.timedelta(minutes=1))
            s.add(sched)
            await s.flush()
            await fire_due_schedules(s, now)
            run_ids = await dispatch_due_agent_tasks(s, now)
            run = await s.get(Run, (tid, run_ids[0]))
            assert run is not None

            provider = MockProvider(script=[[TextDelta("done"), Finish("stop")]])
            await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            # The trace projector copies run_kind='scheduled_task' into traces.trace_kind;
            # this must not violate ck_traces_kind (else the whole run rolls back → failed).
            await project_run_trace(s, tenant_id=tid, run_id=run_ids[0])
            await s.flush()
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_failed_run_settles_firing_failed() -> None:
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
            run_ids = await dispatch_due_agent_tasks(s, now)

            # Simulate a failed run (no assistant output): mark the run failed.
            run = await s.get(Run, (tid, run_ids[0]))
            assert run is not None
            run.status = "failed"
            run.settled_at = datetime.datetime.now(_UTC)
            await s.flush()

            outcome = await settle_scheduled_firing(s, run_ids[0])
            assert outcome == "failed"
            firing = await s.get(ScheduleFiring, (tid, created[0]))
            assert firing is not None and firing.delivery_outcome == "failed"
        finally:
            await s.rollback()
