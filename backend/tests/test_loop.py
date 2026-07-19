"""End-to-end core loop: tool_use turn then final answer, with a scripted mock.

Integration test — skips when no database is reachable; rolls back.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import EffectInvocation, EventJournal, Message, Part, Run, Tenant, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.tools import build_default_registry


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, Run]:
    tid, uid, sid, rid = (uuid.uuid4() for _ in range(4))
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
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
    # initial user message
    mid = uuid.uuid4()
    s.add(
        Message(
            tenant_id=tid,
            id=mid,
            session_id=sid,
            run_id=rid,
            author_user_id=uid,
            seq=1,
            role="user",
        )
    )
    await s.flush()
    s.add(
        Part(
            tenant_id=tid,
            id=uuid.uuid4(),
            message_id=mid,
            ordinal=0,
            kind="text",
            content_redacted={"text": "what time is it?"},
        )
    )
    await s.flush()
    return tid, rid, run


@pytest.mark.asyncio
async def test_loop_tool_then_answer() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, rid, run = await _seed(s)
            provider = MockProvider(
                script=[
                    [ToolCall(id="c1", name="get_time", args={}), Finish("tool_use")],
                    [TextDelta("It is time to work."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )

            assert reason == "completed"
            assert run.status == "succeeded"
            assert run.settled_at is not None

            types = set(
                (
                    await s.execute(
                        select(EventJournal.event_type).where(
                            EventJournal.tenant_id == tid, EventJournal.run_id == rid
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {
                "run.started",
                "tool-call",
                "tool-result",
                "text-delta",
                "turn.end",
                "run.settled",
            } <= types

            assistant = (
                (
                    await s.execute(
                        select(Message).where(
                            Message.tenant_id == tid,
                            Message.run_id == rid,
                            Message.role == "assistant",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(assistant) == 1

            inv = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid, EffectInvocation.run_id == rid
                    )
                )
            ).scalar_one()
            assert inv.effect_name == "get_time"
            assert inv.status == "settled"
            assert inv.outcome == "succeeded"
        finally:
            await s.rollback()
