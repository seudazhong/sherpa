"""Schedule tools + service (m-tools T6): agent sets reminders and digests."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import Run, Schedule, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.services import CallerContext, Invalid, NotFound, VersionConflict, schedules, todos
from app.tools import ToolContext, build_default_registry


async def _seed_base(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


def _future() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)


@pytest.mark.asyncio
async def test_schedule_service_crud_and_errors() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")
            todo = await todos.create_todo(s, ctx, title="Ship it")

            with pytest.raises(NotFound):
                await schedules.create_schedule(
                    s,
                    ctx,
                    kind="todo_reminder",
                    name="r",
                    todo_id=uuid.uuid4(),
                    reminder_kind="due_soon",
                    next_fire_at=_future(),
                )
            with pytest.raises(Invalid):
                await schedules.create_schedule(
                    s,
                    ctx,
                    kind="todo_reminder",
                    name="r",
                    todo_id=todo.id,
                    reminder_kind="due_soon",
                    next_fire_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
                )

            rem = await schedules.create_schedule(
                s,
                ctx,
                kind="todo_reminder",
                name="Remind",
                todo_id=todo.id,
                reminder_kind="due_soon",
                next_fire_at=_future(),
            )
            assert rem.kind == "todo_reminder" and rem.todo_id == todo.id

            dig = await schedules.create_schedule(
                s,
                ctx,
                kind="daily_digest",
                name="Digest",
                local_time=datetime.time(8, 0),
                timezone="Asia/Shanghai",
            )
            assert dig.kind == "daily_digest" and dig.next_fire_at > datetime.datetime.now(
                datetime.UTC
            )

            page = await schedules.list_schedules(s, ctx)
            assert len(page.items) == 2

            with pytest.raises(VersionConflict):
                await schedules.cancel_schedule(s, ctx, schedule_id=rem.id, if_version=999)
            cancelled = await schedules.cancel_schedule(
                s, ctx, schedule_id=rem.id, if_version=rem.version
            )
            assert cancelled.status == "disabled"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_schedule_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")
            todo = await todos.create_todo(s, ctx, title="Prep")
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            made = await reg.get("schedule.create_reminder").execute(
                tctx, {"todo_id": str(todo.id), "remind_at": _future().isoformat()}
            )
            assert "created reminder" in made.llm_content

            dig = await reg.get("schedule.create_digest").execute(
                tctx, {"local_time": "08:30", "timezone": "UTC"}
            )
            assert "daily digest" in dig.llm_content

            listing = await reg.get("schedule.list").execute(tctx, {})
            assert "schedules:" in listing.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_loop_agent_creates_digest() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            sid, rid = uuid.uuid4(), uuid.uuid4()
            s.add(
                SessionModel(
                    tenant_id=tid,
                    id=sid,
                    user_id=uid,
                    umo_key=f"web:chat:{sid}",
                    channel="web",
                    channel_installation_id="local",
                    scope_type="chat",
                    external_scope_id=str(sid),
                )
            )
            await s.flush()
            run = Run(
                tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1"
            )
            s.add(run)
            await s.flush()

            provider = MockProvider(
                script=[
                    [
                        ToolCall(
                            id="c1",
                            name="schedule.create_digest",
                            args={"local_time": "09:00", "timezone": "UTC"},
                        ),
                        Finish("tool_use"),
                    ],
                    [TextDelta("Set."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"
            total = await s.scalar(
                select(func.count()).select_from(Schedule).where(Schedule.tenant_id == tid)
            )
            assert total == 1  # agent created a schedule
        finally:
            await s.rollback()
