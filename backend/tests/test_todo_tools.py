"""Todo tools + service + REST (m-tools T4).

Proves standalone agent todos work across service, tools, the loop, and the new
POST /todos endpoint (migration 0014 relaxed the todos schema). Integration test —
skips without Postgres+Redis.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.main import app
from app.models import Run, Tenant, Todo, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.redis_client import ping_redis
from app.services import CallerContext, NotFound, VersionConflict, todos
from app.tools import ToolContext, build_default_registry
from tests.db_guard import drop_owner_tenant


async def _seed_base(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_todo_service_crud_and_errors() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            todo = await todos.create_todo(s, ctx, title="Write the report", priority="high")
            assert todo.status == "open" and todo.source_candidate_id is None

            page = await todos.list_todos(s, ctx)
            assert any(t.id == todo.id for t in page.items)

            with pytest.raises(VersionConflict):
                await todos.update_todo(s, ctx, todo_id=todo.id, if_version=999, title="x")
            with pytest.raises(NotFound):
                await todos.complete_todo(s, ctx, todo_id=uuid.uuid4(), if_version=1)

            done = await todos.complete_todo(s, ctx, todo_id=todo.id, if_version=todo.version)
            assert done.status == "completed" and done.completed_at is not None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_todo_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            created = await reg.get("todo_write").execute(tctx, {"title": "Buy milk"})
            assert "created todo" in created.llm_content
            todo = (await s.execute(select(Todo).where(Todo.tenant_id == tid))).scalar_one()
            assert todo.title == "Buy milk" and todo.source == "agent"

            listing = await reg.get("list_todos").execute(tctx, {})
            assert str(todo.id) in listing.llm_content

            done = await reg.get("complete_todo").execute(
                tctx, {"todo_id": str(todo.id), "if_version": todo.version}
            )
            assert "completed todo" in done.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_loop_agent_creates_todo() -> None:
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
                        ToolCall(id="c1", name="todo_write", args={"title": "Prep slides"}),
                        Finish("tool_use"),
                    ],
                    [TextDelta("Added."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"
            todo = (await s.execute(select(Todo).where(Todo.tenant_id == tid))).scalar_one()
            assert todo.title == "Prep slides" and todo.source == "agent"
        finally:
            await s.rollback()


async def _drop_owner() -> None:
    await drop_owner_tenant()


@pytest.mark.asyncio
async def test_post_todos_rest() -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            login = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}

            created = await client.post(
                "/todos", json={"title": "Standalone todo", "priority": "low"}, headers=headers
            )
            assert created.status_code == 201
            assert created.json()["source_candidate_id"] is None
            todo_id = created.json()["id"]

            lst = await client.get("/todos")
            assert any(t["id"] == todo_id for t in lst.json()["items"])

            done = await client.patch(
                f"/todos/{todo_id}", json={"if_version": 1, "status": "completed"}, headers=headers
            )
            assert done.status_code == 200 and done.json()["status"] == "completed"
    finally:
        await _drop_owner()
