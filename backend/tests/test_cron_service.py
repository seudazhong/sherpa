"""General cron service + tool (ADR-031, Phase CRON.4).

Covers creating recurring `agent_task` schedules (cron / interval / daily), cadence
validation, run-now, pause/resume, and the `create_scheduled_task` agent tool.
Integration test — skips without Postgres; rolls back.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal, ping_db
from app.models import Schedule, ScheduleFiring, Tenant, User
from app.services import schedules as svc
from app.services.context import CallerContext
from app.services.errors import Invalid, VersionConflict
from app.tools import ToolContext, build_default_registry


async def _seed(s) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return tid, uid


def _ctx(tid, uid):  # type: ignore[no-untyped-def]
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


@pytest.mark.asyncio
async def test_create_agent_task_cron() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            sched = await svc.create_schedule(
                s,
                _ctx(tid, uid),
                kind="agent_task",
                name="Weekday triage",
                prompt="Summarize unread email.",
                cadence_kind="cron",
                cron_expr="0 9 * * 1-5",
                delivery_channel="web",
                timezone="Asia/Shanghai",
            )
            assert sched.kind == "agent_task"
            assert sched.cadence_kind == "cron" and sched.cron_expr == "0 9 * * 1-5"
            assert sched.next_fire_at > datetime.datetime.now(datetime.UTC)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_agent_task_validation_errors() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            ctx = _ctx(tid, uid)
            # No prompt.
            with pytest.raises(Invalid):
                await svc.create_schedule(
                    s,
                    ctx,
                    kind="agent_task",
                    name="x",
                    prompt="",
                    cadence_kind="daily",
                    local_time=datetime.time(9, 0),
                )
            # Interval below the service min-frequency floor (300s).
            with pytest.raises(Invalid):
                await svc.create_schedule(
                    s,
                    ctx,
                    kind="agent_task",
                    name="x",
                    prompt="do it",
                    cadence_kind="interval",
                    interval_seconds=60,
                )
            # Daily without a local_time.
            with pytest.raises(Invalid):
                await svc.create_schedule(
                    s,
                    ctx,
                    kind="agent_task",
                    name="x",
                    prompt="do it",
                    cadence_kind="daily",
                )
            # Invalid cron expression.
            with pytest.raises(Invalid):
                await svc.create_schedule(
                    s,
                    ctx,
                    kind="agent_task",
                    name="x",
                    prompt="do it",
                    cadence_kind="cron",
                    cron_expr="not a cron",
                )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_now_and_pause_resume() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            ctx = _ctx(tid, uid)
            sched = await svc.create_schedule(
                s,
                ctx,
                kind="agent_task",
                name="Task",
                prompt="do it",
                cadence_kind="interval",
                interval_seconds=600,
            )

            firing = await svc.run_now(s, ctx, schedule_id=sched.id)
            assert firing.status == "pending"
            count = await s.scalar(
                select(ScheduleFiring.id).where(ScheduleFiring.schedule_id == sched.id)
            )
            assert count is not None

            paused = await svc.set_status(
                s, ctx, schedule_id=sched.id, if_version=sched.version, status="paused"
            )
            assert paused.status == "paused"
            resumed = await svc.set_status(
                s, ctx, schedule_id=sched.id, if_version=paused.version, status="active"
            )
            assert resumed.status == "active"

            with pytest.raises(VersionConflict):
                await svc.set_status(s, ctx, schedule_id=sched.id, if_version=999, status="paused")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_create_scheduled_task_tool() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            res = await reg.get("create_scheduled_task").execute(
                tctx,
                {
                    "name": "Nightly report",
                    "prompt": "Summarize today's activity.",
                    "cron": "0 22 * * *",
                    "delivery_channel": "web",
                },
            )
            assert "scheduled task" in res.llm_content
            row = (await s.execute(select(Schedule).where(Schedule.tenant_id == tid))).scalar_one()
            assert row.kind == "agent_task" and row.cadence_kind == "cron"
            assert row.prompt == "Summarize today's activity."
        finally:
            await s.rollback()
