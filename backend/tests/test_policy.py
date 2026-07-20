"""ALLOWED policy engine (m-tools T2): evaluate() + loop deny/ask/allow wiring.

Pure unit tests for the decision table, plus one loop integration proving a `deny`
decision refuses execution (invocation settled failed, tool-error observed).
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
from app.permissions import policy
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.tools import ToolFlags
from app.tools.builtin import EchoTool, GetTimeTool, SendEmailTool


class _Dummy:
    def __init__(self, flags: ToolFlags) -> None:
        self.flags = flags


def test_evaluate_read_only_allows() -> None:
    assert policy.evaluate(EchoTool()) == "allow"
    assert policy.evaluate(GetTimeTool()) == "allow"


def test_evaluate_own_tenant_write_allows() -> None:
    idempotent = _Dummy(
        ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)
    )
    assert policy.evaluate(idempotent) == "allow"  # type: ignore[arg-type]


def test_evaluate_external_action_asks() -> None:
    assert policy.evaluate(SendEmailTool()) == "ask"
    assert policy.requires_approval(SendEmailTool()) is True
    assert policy.requires_approval(EchoTool()) is False


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
            content_redacted={"text": "hi"},
        )
    )
    await s.flush()
    return tid, rid, run


@pytest.mark.asyncio
async def test_loop_deny_refuses_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(policy, "evaluate", lambda tool: "deny")
    async with SessionLocal() as s:
        try:
            tid, rid, run = await _seed(s)
            provider = MockProvider(
                script=[
                    [ToolCall(id="c1", name="get_time", args={}), Finish("tool_use")],
                    [TextDelta("ok"), Finish("stop")],
                ]
            )
            from app.tools import build_default_registry

            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"

            inv = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid, EffectInvocation.run_id == rid
                    )
                )
            ).scalar_one()
            assert inv.effect_name == "get_time"
            assert inv.status == "settled" and inv.outcome == "failed"  # denied, never ran

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
            assert "tool-error" in types
        finally:
            await s.rollback()
