"""Approval closure: resume executes (allow) or fails (reject) the gated action.

Drives `resume_approval` directly with the test session (no commit → rollback),
seeding the full gated state (tool-call event + prepared invocation + decided
envelope). Integration test: skips without a database.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.resume import resume_approval
from app.db import SessionLocal, ping_db
from app.effects import begin_invocation
from app.events import append_event
from app.models import AuditReceipt, EffectInvocation, EventJournal, Run, Tenant, User
from app.models import Session as SessionModel
from app.permissions import request_approval
from app.permissions.policy import classify_effect
from app.tools.builtin import SendEmailTool

_ARGS: dict[str, object] = {"to": "a@b.co", "subject": "Hi", "body": "Hello there"}
_CALL_ID = "call_send_1"


async def _seed_gated_send(
    s: AsyncSession, *, decision: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed a decided approval for a gated send_email; return (tenant, invocation, correlation)."""
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
    s.add(Run(tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1"))
    await s.flush()

    await append_event(
        s,
        tenant_id=tid,
        run_id=rid,
        session_id=sid,
        event_type="tool-call",
        payload={"id": _CALL_ID, "name": "email_send", "args": _ARGS},
    )

    effect_class = classify_effect(SendEmailTool().flags)
    handle = await begin_invocation(
        s,
        tenant_id=tid,
        run_id=rid,
        effect_name="email_send",
        idempotency_key=f"tool:{rid}:1:{_CALL_ID}",
        effect_class=effect_class,
        retry_policy="transient_before_dispatch",
        args=_ARGS,
        turn_seq=1,
    )
    created = await request_approval(
        s,
        tenant_id=tid,
        run_id=rid,
        session_id=sid,
        invocation_id=handle.invocation_id,
        tool_name="email_send",
        effect_class=effect_class,
        args=_ARGS,
        decider_user_id=uid,
    )
    env = created.envelope
    env.status = "decided"
    env.decision = decision
    env.decided_by_user_id = uid
    env.decided_via_channel = "web"
    env.decided_at = datetime.datetime.now(datetime.UTC)
    await s.flush()
    return tid, handle.invocation_id, env.correlation_id


async def _events(s: AsyncSession, tid: uuid.UUID, event_type: str) -> list[dict[str, object]]:
    rows = (
        (
            await s.execute(
                select(EventJournal.payload_redacted).where(
                    EventJournal.tenant_id == tid, EventJournal.event_type == event_type
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@pytest.mark.asyncio
async def test_resume_allow_executes_send_email() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, cid = await _seed_gated_send(s, decision="allow_once")

            assert await resume_approval(s, cid) == "resumed"

            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None
            assert inv.status == "settled" and inv.outcome == "succeeded"

            results = await _events(s, tid, "tool-result")
            assert any(
                p.get("id") == _CALL_ID and "email sent to a@b.co" in str(p.get("output", ""))
                for p in results
            )

            receipts = (
                (
                    await s.execute(
                        select(AuditReceipt).where(
                            AuditReceipt.tenant_id == tid, AuditReceipt.action == "email_send"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert any(r.outcome == "succeeded" and r.receipt_type == "action" for r in receipts)

            # Idempotent: a redelivered resume is a no-op once settled.
            assert await resume_approval(s, cid) == "already_settled"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_resume_reject_fails_invocation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, cid = await _seed_gated_send(s, decision="reject")

            assert await resume_approval(s, cid) == "rejected"

            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None
            assert inv.status == "settled" and inv.outcome == "failed"

            errors = await _events(s, tid, "tool-error")
            assert any("approval_rejected" in str(p.get("output", "")) for p in errors)

            # No send_email was executed → no tool-result.
            assert await _events(s, tid, "tool-result") == []
        finally:
            await s.rollback()
