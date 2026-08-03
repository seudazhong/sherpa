"""Approval closure: resume executes (allow) or fails (reject) the gated action.

Drives `resume_approval` directly with the test session (no commit → rollback),
seeding the full gated state (tool-call event + prepared invocation + decided
envelope). Integration test: skips without a database.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.core.resume import recover_stale_approval_resumes, resume_approval
from app.db import SessionLocal, ping_db
from app.effects import begin_invocation
from app.events import append_event
from app.models import (
    AuditReceipt,
    EffectInvocation,
    EventJournal,
    Message,
    Part,
    Run,
    Tenant,
    User,
)
from app.models import Session as SessionModel
from app.permissions import request_approval
from app.permissions.policy import classify_effect
from app.providers import Finish, MockProvider, TextDelta
from app.tools import build_default_registry
from app.tools.builtin import SendEmailTool
from app.worker import approval_resume_job, approval_resume_tick, run_job

_ARGS: dict[str, object] = {"to": "a@b.co", "subject": "Hi", "body": "Hello there"}
_CALL_ID = "call_send_1"


async def _seed_gated_send(
    s: AsyncSession, *, decision: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed a suspended run with one decided, gated send_email invocation."""
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
    s.add(
        Run(
            tenant_id=tid,
            id=rid,
            session_id=sid,
            run_kind="web_chat",
            prompt_version="v1",
            admitted_seq=1,
            status="running",
        )
    )
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
            content_redacted={"text": "Send the message."},
        )
    )
    await s.flush()

    await append_event(
        s,
        tenant_id=tid,
        run_id=rid,
        session_id=sid,
        event_type="run.started",
        payload={"run_kind": "web_chat"},
    )
    await append_event(
        s,
        tenant_id=tid,
        run_id=rid,
        session_id=sid,
        event_type="tool-call",
        payload={"id": _CALL_ID, "name": "email_send", "args": _ARGS},
    )
    await append_event(
        s,
        tenant_id=tid,
        run_id=rid,
        session_id=sid,
        event_type="turn.end",
        payload={"turn": 1},
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
    await append_event(
        s,
        tenant_id=tid,
        run_id=rid,
        session_id=sid,
        event_type="permission.asked",
        payload={
            "id": _CALL_ID,
            "observation": "permission_required: awaiting approval",
            "correlation_id": str(env.correlation_id),
        },
    )
    await s.flush()
    return tid, handle.invocation_id, env.correlation_id, rid


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


async def _cleanup_tenant(s: AsyncSession, tid: uuid.UUID) -> None:
    await s.rollback()
    await s.execute(delete(Tenant).where(Tenant.tenant_id == tid))
    await s.commit()


@pytest.mark.asyncio
async def test_resume_allow_executes_send_email() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, cid, rid = await _seed_gated_send(s, decision="allow_once")

            assert await resume_approval(s, cid) == "resumed"

            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None
            assert inv.status == "settled" and inv.outcome == "succeeded"
            run = await s.get(Run, (tid, rid))
            assert run is not None and run.status == "queued" and run.settled_at is None

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

            # Idempotent recovery: if the effect/result commit won but the worker
            # crashed before queuing continuation, redelivery re-arms the run.
            run.status = "running"
            await s.flush()
            assert await resume_approval(s, cid) == "already_settled"
            assert run.status == "queued"
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_resume_reject_fails_invocation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, cid, rid = await _seed_gated_send(s, decision="reject")

            assert await resume_approval(s, cid) == "rejected"

            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None
            assert inv.status == "settled" and inv.outcome == "failed"
            run = await s.get(Run, (tid, rid))
            assert run is not None and run.status == "queued" and run.settled_at is None

            errors = await _events(s, tid, "tool-error")
            assert any(
                p.get("id") == _CALL_ID and "approval_rejected" in str(p.get("output", ""))
                for p in errors
            )

            # No send_email was executed → no tool-result.
            assert await _events(s, tid, "tool-result") == []
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_identical_approval_calls_wait_until_both_invocations_settle() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, _inv_id, cid1, rid = await _seed_gated_send(s, decision="allow_once")
            run = await s.get(Run, (tid, rid))
            assert run is not None and run.session_id is not None
            sess = await s.get(SessionModel, (tid, run.session_id))
            assert sess is not None and sess.user_id is not None

            call_id2 = "call_send_2"
            await append_event(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=run.session_id,
                event_type="tool-call",
                payload={"id": call_id2, "name": "email_send", "args": _ARGS},
            )
            handle2 = await begin_invocation(
                s,
                tenant_id=tid,
                run_id=rid,
                effect_name="email_send",
                idempotency_key=f"tool:{rid}:2:{call_id2}",
                effect_class=classify_effect(SendEmailTool().flags),
                retry_policy="transient_before_dispatch",
                args=_ARGS,
                turn_seq=2,
            )
            created2 = await request_approval(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=run.session_id,
                invocation_id=handle2.invocation_id,
                tool_name="email_send",
                effect_class=classify_effect(SendEmailTool().flags),
                args=_ARGS,
                decider_user_id=sess.user_id,
            )
            env2 = created2.envelope
            env2.status = "decided"
            env2.decision = "allow_once"
            env2.decided_by_user_id = sess.user_id
            env2.decided_via_channel = "web"
            env2.decided_at = datetime.datetime.now(datetime.UTC)
            await s.flush()

            assert await resume_approval(s, cid1) == "resumed"
            assert run.status == "running"
            assert await resume_approval(s, env2.correlation_id) == "resumed"
            assert run.status == "queued"

            results = await _events(s, tid, "tool-result")
            result_ids = {str(payload.get("id", "")) for payload in results}
            assert {_CALL_ID, call_id2} <= result_ids
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_running_approval_invocation_is_not_dispatched_again() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, cid, _rid = await _seed_gated_send(s, decision="allow_once")
            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None
            inv.status = "running"
            inv.started_at = datetime.datetime.now(datetime.UTC)
            await s.flush()

            assert await resume_approval(s, cid) == "already_running"
            assert await _events(s, tid, "tool-result") == []
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_prepared_approval_does_not_execute_after_run_needs_reconciliation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, cid, rid = await _seed_gated_send(s, decision="allow_once")
            run = await s.get(Run, (tid, rid))
            assert run is not None
            run.status = "needs_reconciliation"
            run.settled_at = datetime.datetime.now(datetime.UTC)
            await s.flush()

            assert await resume_approval(s, cid) == "run_not_suspended:needs_reconciliation"
            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None and inv.status == "prepared"
            assert await _events(s, tid, "tool-result") == []
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_stale_running_approval_becomes_effect_unknown() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, inv_id, _cid, rid = await _seed_gated_send(s, decision="allow_once")
            inv = await s.get(EffectInvocation, (tid, inv_id))
            assert inv is not None
            old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
            inv.status = "running"
            inv.started_at = old
            inv.updated_at = old
            await s.flush()

            assert await recover_stale_approval_resumes(
                s, now=datetime.datetime.now(datetime.UTC)
            ) == [rid]
            await s.refresh(inv)
            assert inv.status == "needs_reconciliation"
            assert inv.outcome == "effect_unknown"
            run = await s.get(Run, (tid, rid))
            assert run is not None and run.status == "needs_reconciliation"
            event_types = (
                (
                    await s.execute(
                        select(EventJournal.event_type)
                        .where(EventJournal.tenant_id == tid, EventJournal.run_id == rid)
                        .order_by(EventJournal.run_seq)
                    )
                )
                .scalars()
                .all()
            )
            assert event_types[-2:] == ["tool-error", "run.settled"]
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_approval_on_nominal_last_turn_gets_one_continuation_call() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, _inv_id, cid, rid = await _seed_gated_send(s, decision="allow_once")
            assert await resume_approval(s, cid) == "resumed"
            run = await s.get(Run, (tid, rid))
            assert run is not None

            reason = await execute_run(
                s,
                run=run,
                provider=MockProvider(
                    script=[[TextDelta("The approved action completed."), Finish("stop")]]
                ),
                registry=build_default_registry(),
                max_turns=1,
            )
            assert reason == "completed"
            messages = (
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
            assert len(messages) == 1
        finally:
            await _cleanup_tenant(s, tid)


@pytest.mark.asyncio
async def test_approval_resume_job_requeues_and_agent_reports_result(monkeypatch) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")

    async with SessionLocal() as s:
        tid, _inv_id, cid, rid = await _seed_gated_send(s, decision="allow_once")
        await s.commit()

    queued: list[uuid.UUID] = []
    resume_queued: list[uuid.UUID] = []

    async def fake_enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    async def fake_resume_enqueue(correlation_id: uuid.UUID) -> None:
        resume_queued.append(correlation_id)

    async def leader(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_provider(*_args: object, **_kwargs: object) -> MockProvider:
        return MockProvider(
            script=[
                [
                    TextDelta("The approved action completed successfully."),
                    Finish("stop"),
                ]
            ]
        )

    monkeypatch.setattr("app.worker.queue.enqueue_run", fake_enqueue)
    monkeypatch.setattr("app.worker.queue.enqueue_approval_resume", fake_resume_enqueue)
    monkeypatch.setattr("app.worker.try_acquire_leader", leader)
    monkeypatch.setattr("app.worker.provider_for_session", fake_provider)
    try:
        assert await approval_resume_tick({}) == "redispatched=1 effect_unknown=0"
        assert resume_queued == [cid]

        assert await approval_resume_job({}, str(cid)) == "resumed"
        assert queued == [rid]

        assert await run_job({}, str(rid)) == "completed"
        assert await run_job({}, str(rid)) == "not_queued"
        async with SessionLocal() as check:
            run = await check.get(Run, (tid, rid))
            assert run is not None and run.status == "succeeded"
            messages = (
                (
                    await check.execute(
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
            assert len(messages) == 1
            part = await check.scalar(
                select(Part.content_redacted).where(
                    Part.tenant_id == tid, Part.message_id == messages[0].id
                )
            )
            assert part == {"text": "The approved action completed successfully."}
    finally:
        async with SessionLocal() as cleanup:
            await cleanup.execute(delete(Tenant).where(Tenant.tenant_id == tid))
            await cleanup.commit()
