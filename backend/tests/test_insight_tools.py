"""Read + settings tools + service (m-tools T7)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import READ, record_receipt
from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.services import CallerContext, VersionConflict, insights
from app.tools import ToolContext, build_default_registry


async def _seed_base(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_insights_service() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            await record_receipt(
                s,
                tenant_id=tid,
                receipt_type=READ,
                actor_type="connector",
                trigger_type="sync",
                action="gmail_sync",
                outcome="succeeded",
                subject_type="connector",
                subject_id=uuid.uuid4(),
                summary={"seen": 1},
            )
            acts = await insights.list_activity(s, ctx)
            assert any(a.action == "gmail_sync" for a in acts.items)

            notifs = await insights.list_notifications(s, ctx)
            assert notifs.items == []

            settings = await insights.get_settings(s, ctx)
            assert settings.version >= 1
            with pytest.raises(VersionConflict):
                await insights.update_settings(s, ctx, if_version=999, notifications_enabled=True)
            updated = await insights.update_settings(
                s, ctx, if_version=settings.version, notifications_enabled=True, daily_cap=3
            )
            assert updated.notifications_enabled is True and updated.daily_cap == 3
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_insight_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            got = await reg.get("get_settings").execute(tctx, {})
            assert "version=" in got.llm_content

            updated = await reg.get("update_settings").execute(
                tctx, {"notifications_enabled": True}
            )
            assert "updated settings" in updated.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_loop_agent_updates_settings() -> None:
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
                            id="c1", name="update_settings", args={"notifications_enabled": True}
                        ),
                        Finish("tool_use"),
                    ],
                    [TextDelta("Enabled."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"
            settings = await insights.get_settings(
                s, CallerContext(tenant_id=tid, user_id=uid, actor="agent")
            )
            assert settings.notifications_enabled is True
        finally:
            await s.rollback()
