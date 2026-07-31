"""Core-memory service + tools (milestone 1a): set/get/list/delete + versioning.

Integration test — skips without a database; seeds + rolls back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, Message, ProviderEvent, TextDelta, ToolSchema
from app.services import CallerContext, Invalid, NotFound
from app.services import memory as mem
from app.tools import ToolContext, build_default_registry


class _Recorder:
    """Provider that records the messages of each call, then answers trivially."""

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
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


async def _seed_session(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, Run]:
    tid, uid = await _seed(s)
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
    run = Run(tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1")
    s.add(run)
    await s.flush()
    return tid, uid, run


@pytest.mark.asyncio
async def test_memory_service_roundtrip_and_versioning() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            first = await mem.set_memory(s, ctx, key="timezone", value="Asia/Shanghai")
            assert first.version == 1
            got = await mem.get_memory(s, ctx, key="timezone")
            assert got is not None and got.value_text == "Asia/Shanghai"

            # Overwrite bumps the version.
            second = await mem.set_memory(s, ctx, key="timezone", value="UTC")
            assert second.version == 2 and second.value_text == "UTC"

            assert len(await mem.list_memory(s, ctx)) == 1

            await mem.delete_memory(s, ctx, key="timezone")
            assert await mem.get_memory(s, ctx, key="timezone") is None
            with pytest.raises(NotFound):
                await mem.delete_memory(s, ctx, key="timezone")

            # Invalid key / oversized value are clean errors, not DB failures.
            with pytest.raises(Invalid):
                await mem.set_memory(s, ctx, key="Bad Key", value="x")
            with pytest.raises(Invalid):
                await mem.set_memory(s, ctx, key="big", value="x" * 16385)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_memory_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            out = await reg.get("memory_set").execute(
                tctx, {"key": "prefers.concise", "value": "yes"}
            )
            assert "remembered" in out.llm_content

            got = await reg.get("memory_recall").execute(tctx, {"key": "prefers.concise"})
            assert "yes" in got.llm_content

            # key omitted -> recall everything (absorbed `memory_user_list`, B-10)
            listed = await reg.get("memory_recall").execute(tctx, {})
            assert "prefers.concise" in listed.llm_content

            await reg.get("memory_delete").execute(tctx, {"key": "prefers.concise"})
            gone = await reg.get("memory_recall").execute(tctx, {"key": "prefers.concise"})
            assert "no memory" in gone.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_core_memory_injected_into_system_prompt() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, run = await _seed_session(s)
            await mem.set_memory(
                s,
                CallerContext(tenant_id=tid, user_id=uid, actor="agent"),
                key="timezone",
                value="Asia/Shanghai",
            )
            rec = _Recorder()
            await execute_run(
                s, run=run, provider=rec, registry=build_default_registry(), tier="full"
            )
            system = rec.calls[0][0]
            assert system["role"] == "system"
            assert "timezone: Asia/Shanghai" in str(system["content"])
        finally:
            await s.rollback()
