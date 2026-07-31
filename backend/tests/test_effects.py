"""Effect invocation lifecycle: idempotent begin + settle outcomes (ADR-017).

Integration test — skips when no database is reachable; rolls back.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.effects import begin_invocation, mark_running, settle_succeeded, settle_unknown
from app.models import EffectInvocation, Run, Tenant, User
from app.models import Session as SessionModel


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
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
    return tid, rid


@pytest.mark.asyncio
async def test_begin_is_idempotent_and_settles_succeeded() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, rid = await _seed(s)
            key = f"tool:{rid}:0:call-1"
            common = {
                "tenant_id": tid,
                "run_id": rid,
                "effect_name": "run_tests",
                "idempotency_key": key,
                "effect_class": "idempotent_write",
                "retry_policy": "same_key",
                "args": {"cmd": "pytest"},
                "turn_seq": 1,
            }
            h1 = await begin_invocation(s, **common)  # type: ignore[arg-type]
            assert h1.created is True
            assert h1.status == "prepared"

            h2 = await begin_invocation(s, **common)  # type: ignore[arg-type]
            assert h2.created is False
            assert h2.invocation_id == h1.invocation_id

            await mark_running(s, tid, h1.invocation_id)
            await settle_succeeded(s, tid, h1.invocation_id, result={"exit": 0})

            row = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid,
                        EffectInvocation.invocation_id == h1.invocation_id,
                    )
                )
            ).scalar_one()
            assert row.status == "settled"
            assert row.outcome == "succeeded"
            assert row.attempts == 1
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_effect_unknown_moves_to_reconciliation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, rid = await _seed(s)
            h = await begin_invocation(
                s,
                tenant_id=tid,
                run_id=rid,
                effect_name="email_send",
                idempotency_key=f"tool:{rid}:0:send-1",
                effect_class="reconcilable_write",
                retry_policy="after_reconcile",
                args={"to": "maya@example.com"},
            )
            await mark_running(s, tid, h.invocation_id)
            await settle_unknown(s, tid, h.invocation_id, error="timeout after dispatch")

            row = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid,
                        EffectInvocation.invocation_id == h.invocation_id,
                    )
                )
            ).scalar_one()
            assert row.status == "needs_reconciliation"
            assert row.outcome == "effect_unknown"
            assert row.reconciliation_state == "pending"
        finally:
            await s.rollback()
