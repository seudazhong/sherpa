"""Session Library service: browse ordering, resume-state truthfulness, recover.

Integration test — skips when no database is reachable; rolls back.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import ApprovalEnvelope, EffectInvocation, Run, Tenant, User
from app.models import Session as SessionModel
from app.services import sessions as svc
from app.services.context import CallerContext
from app.services.errors import NotFound


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _seed_owner(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return tid, uid


async def _add_session(
    s: AsyncSession,
    tid: uuid.UUID,
    uid: uuid.UUID,
    *,
    activity: datetime.datetime | None,
    status: str = "open",
    title: str | None = None,
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
            status=status,
            title=title,
            last_activity_at=activity,
        )
    )
    await s.flush()
    return sid


async def _add_run(
    s: AsyncSession,
    tid: uuid.UUID,
    sid: uuid.UUID,
    *,
    status: str,
    lease_expires_at: datetime.datetime | None = None,
) -> uuid.UUID:
    rid = uuid.uuid4()
    s.add(
        Run(
            tenant_id=tid,
            id=rid,
            session_id=sid,
            run_kind="web_chat",
            prompt_version="v1",
            status=status,
            started_at=_now(),
            settled_at=None if status in ("queued", "running") else _now(),
            lease_expires_at=lease_expires_at,
        )
    )
    await s.flush()
    return rid


@pytest.mark.asyncio
async def test_browse_orders_by_activity_and_scopes_user() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            other = uuid.uuid4()
            s.add(
                User(tenant_id=tid, id=other, email="x@e.co", display_name="X", status="disabled")
            )
            await s.flush()
            old = await _add_session(s, tid, uid, activity=_now() - datetime.timedelta(hours=2))
            new = await _add_session(s, tid, uid, activity=_now())
            await _add_session(s, tid, other, activity=_now())  # other user's session
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")

            page = await svc.browse(s, ctx, limit=10)
            ids = [v.session.id for v in page.items]
            assert ids == [new, old]  # activity desc, other user's excluded
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_resume_state_running_vs_stale() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            live_sid = await _add_session(s, tid, uid, activity=_now())
            await _add_run(
                s,
                tid,
                live_sid,
                status="running",
                lease_expires_at=_now() + datetime.timedelta(seconds=30),
            )
            stale_sid = await _add_session(s, tid, uid, activity=_now())
            await _add_run(
                s,
                tid,
                stale_sid,
                status="running",
                lease_expires_at=_now() - datetime.timedelta(seconds=30),
            )

            live = await svc.get_view(s, ctx, live_sid)
            stale = await svc.get_view(s, ctx, stale_sid)
            assert live.resume_state == "running" and live.live is True
            assert stale.resume_state == "stale" and stale.live is False
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_resume_state_expired_approval_not_actionable() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            sid = await _add_session(s, tid, uid, activity=_now())
            rid = await _add_run(s, tid, sid, status="succeeded")
            inv_id = uuid.uuid4()
            s.add(
                EffectInvocation(
                    tenant_id=tid,
                    invocation_id=inv_id,
                    run_id=rid,
                    effect_name="email_send",
                    idempotency_key=str(uuid.uuid4()),
                    effect_class="reconcilable_write",
                    retry_policy="after_reconcile",
                    args_hash=b"c" * 32,
                    status="running",
                )
            )
            await s.flush()
            s.add(
                ApprovalEnvelope(
                    tenant_id=tid,
                    id=uuid.uuid4(),
                    envelope_version=1,
                    correlation_id=uuid.uuid4(),
                    run_id=rid,
                    session_id=sid,
                    invocation_id=inv_id,
                    tool_name="email_send",
                    permission_scope="email_send",
                    effect_class="reconcilable_write",
                    args_hash=b"a" * 32,
                    policy_version="v1",
                    requested_at=_now() - datetime.timedelta(minutes=2),
                    expires_at=_now() - datetime.timedelta(minutes=1),
                    nonce_hash=b"b" * 32,
                    preview_redacted={},
                    authorized_decider_user_id=uid,
                    status="pending",
                )
            )
            await s.flush()
            view = await svc.get_view(s, ctx, sid)
            assert view.resume_state == "approval_expired"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_recover_verified_resolves_effect_unknown() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            sid = await _add_session(s, tid, uid, activity=_now())
            rid = await _add_run(s, tid, sid, status="needs_reconciliation")
            s.add(
                EffectInvocation(
                    tenant_id=tid,
                    invocation_id=uuid.uuid4(),
                    run_id=rid,
                    effect_name="email_send",
                    idempotency_key=str(uuid.uuid4()),
                    effect_class="reconcilable_write",
                    retry_policy="after_reconcile",
                    args_hash=b"z" * 32,
                    status="needs_reconciliation",
                    outcome="effect_unknown",
                    reconciliation_state="pending",
                    settled_at=_now(),
                )
            )
            await s.flush()

            before = await svc.get_view(s, ctx, sid)
            assert before.resume_state == "effect_unknown"

            after = await svc.recover(s, ctx, sid, "verified")
            assert after.resume_state == "ready"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_rename_and_cross_user_notfound() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            sid = await _add_session(s, tid, uid, activity=_now())
            owner_ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            # A different user_id in the same tenant must not see this session.
            other_ctx = CallerContext(tenant_id=tid, user_id=uuid.uuid4(), actor="user")

            view = await svc.rename(s, owner_ctx, sid, "My renamed chat")
            assert view.session.title == "My renamed chat"

            with pytest.raises(NotFound):
                await svc.get_view(s, other_ctx, sid)
        finally:
            await s.rollback()
