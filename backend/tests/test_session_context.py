"""Ambient session context in the assembled prompt (backlog B-3).

A project-bound chat must be able to answer "which project is this?" from its own
context instead of guessing from `list_projects`, and the project tools must default to
that binding. Integration test — skips without Postgres. Rolls back.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.core.session_context import render_session_context, surface_label
from app.db import SessionLocal, ping_db
from app.models import Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, Message, ProviderEvent, TextDelta, ToolSchema
from app.services import CallerContext
from app.services import projects as proj_svc
from app.tools import ToolContext, build_default_registry
from app.tools.base import ToolError


class _Recorder:
    """Provider that records each call's messages, then answers trivially."""

    name = "rec"

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.calls.append([dict(m) for m in messages])
        yield TextDelta("ok")
        yield Finish("stop")


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return tid, uid


async def _session(
    s: AsyncSession, tid: uuid.UUID, uid: uuid.UUID, *, project_id: uuid.UUID | None = None
) -> uuid.UUID:
    sid = uuid.uuid4()
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
            status="open",
            project_id=project_id,
        )
    )
    await s.flush()
    return sid


def test_surface_label_translates_umo_pairs() -> None:
    # Never show raw UMO keys to the model (docs/reviews/ui-design-review.md).
    assert surface_label("web", "chat") == "Web chat"
    assert surface_label("qq", "group") == "QQ group"
    assert surface_label("slack", "channel") == "slack · channel"


@pytest.mark.asyncio
async def test_general_chat_context_says_no_project() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            sid = await _session(s, tid, uid)
            block = await render_session_context(s, tenant_id=tid, session_id=sid)
            assert "Surface: Web chat" in block
            assert "Project: none" in block
            assert "project.list" in block
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_bound_chat_context_names_the_project_and_pins_the_date() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            cc = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            project = await proj_svc.create_project(s, cc, name="helloworld")
            sid = await _session(s, tid, uid, project_id=project.id)

            block = await render_session_context(
                s,
                tenant_id=tid,
                session_id=sid,
                now=datetime.datetime(2026, 7, 28, 9, 30, tzinfo=datetime.UTC),
            )
            assert "helloworld" in block
            assert str(project.id) in block
            assert "bound to it" in block
            # Date only: a wall-clock stamp would churn the cacheable prefix every run.
            assert "2026-07-28" in block
            assert "09:30" not in block
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_unknown_session_renders_nothing() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, _ = await _seed(s)
            assert await render_session_context(s, tenant_id=tid, session_id=uuid.uuid4()) == ""
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_project_tools_default_to_the_bound_project() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            cc = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            project = await proj_svc.create_project(s, cc, name="bound", template_id="python-basic")
            sid = await _session(s, tid, uid, project_id=project.id)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session_id=sid, session=s)

            # No project_id passed: the binding supplies it.
            tree = await reg.get("project.tree").execute(tctx, {})
            assert "main.py" in tree.llm_content
            read = await reg.get("project.read").execute(tctx, {"path": "main.py"})
            assert "hello, sherpa" in read.llm_content
            # The listing marks which project the chat is on.
            listing = await reg.get("project.list").execute(tctx, {})
            assert "this chat is bound to this project" in listing.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_general_chat_without_project_id_gets_an_actionable_observation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            sid = await _session(s, tid, uid)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session_id=sid, session=s)
            with pytest.raises(ToolError) as err:
                await reg.get("project.tree").execute(tctx, {})
            assert "not bound to a project" in str(err.value)
            assert "project.list" in str(err.value)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sends_the_binding_to_the_provider() -> None:
    # The whole point of B-3: the model receives the binding, so it never has to guess.
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            cc = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            project = await proj_svc.create_project(s, cc, name="prompted")
            sid = await _session(s, tid, uid, project_id=project.id)
            run = Run(
                tenant_id=tid,
                id=uuid.uuid4(),
                session_id=sid,
                run_kind="web_chat",
                prompt_version="v1",
            )
            s.add(run)
            await s.flush()

            rec = _Recorder()
            await execute_run(s, run=run, provider=rec, registry=build_default_registry())

            system = rec.calls[0][0]
            assert system["role"] == "system"
            content = str(system["content"])
            assert "prompted" in content and str(project.id) in content
            assert "Surface: Web chat" in content
            # Layer order (docs/04): the static prefix stays first and byte-stable.
            assert content.startswith("You are Sherpa")
        finally:
            await s.rollback()
