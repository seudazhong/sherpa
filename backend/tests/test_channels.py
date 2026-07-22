"""QQ + email channel tests (ADR-026/027/028).

Unit tests (no I/O): command parsing, reply composition. DB tests (skip without
Postgres): session mapping, admission idempotency, reply delivery, pending-approval
matching. API tests (skip without Postgres+Redis): simulate, status, transcript —
enqueue is monkeypatched and the outbound client is a RecordingQQClient, so tests
never touch Redis-jobs or QQ. Official QQ has no HTTP webhook (WS in the worker).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import owner_ids
from app.channels import (
    RecordingQQClient,
    admit_inbound,
    compose_reply,
    deliver_run_reply,
    ensure_channel_session,
    find_pending_approval,
    parse_command,
)
from app.channels.qq import build_qq_client
from app.config import settings
from app.db import SessionLocal, ping_db
from app.effects import begin_invocation
from app.main import app
from app.models import Message, Part, Run, Tenant, User
from app.models import Session as SessionModel
from app.permissions import request_approval
from app.permissions.policy import classify_effect
from app.redis_client import ping_redis
from app.services import channels as chan_svc
from app.tools.builtin import SendEmailTool

# --------------------------------------------------------------------------- #
# Unit — no I/O.                                                               #
# --------------------------------------------------------------------------- #


def test_parse_command() -> None:
    assert parse_command("approve").choice == "allow_once"  # type: ignore[union-attr]
    assert parse_command("approve 1a2b").correlation_prefix == "1a2b"  # type: ignore[union-attr]
    assert parse_command("reject 9f").choice == "reject"  # type: ignore[union-attr]
    assert parse_command("yes").choice == "allow_once"  # type: ignore[union-attr]
    assert parse_command("no").choice == "reject"  # type: ignore[union-attr]
    assert parse_command("同意").choice == "allow_once"  # type: ignore[union-attr]
    assert parse_command("please summarize my inbox") is None
    assert parse_command("") is None


def test_compose_reply_plain() -> None:
    assert compose_reply("hello there", None) == "hello there"
    assert compose_reply(None, None) == "(no reply)"


def test_build_qq_client_defaults_to_recording() -> None:
    assert isinstance(build_qq_client(), RecordingQQClient)


# --------------------------------------------------------------------------- #
# DB helpers + tests.                                                          #
# --------------------------------------------------------------------------- #


async def _seed_owner_like(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_ensure_channel_session_idempotent() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner_like(s)
            a = await ensure_channel_session(
                s,
                tenant_id=tid,
                user_id=uid,
                channel="qq",
                installation_id="bot",
                external_id="10001",
            )
            b = await ensure_channel_session(
                s,
                tenant_id=tid,
                user_id=uid,
                channel="qq",
                installation_id="bot",
                external_id="10001",
            )
            assert a.id == b.id
            assert a.channel == "qq" and a.external_scope_id == "10001"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_admit_inbound_creates_run_and_is_idempotent() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner_like(s)
            sess = await ensure_channel_session(
                s,
                tenant_id=tid,
                user_id=uid,
                channel="qq",
                installation_id="bot",
                external_id="10001",
            )
            first = await admit_inbound(
                s, sess=sess, user_id=uid, text="hi", external_message_id="m1"
            )
            again = await admit_inbound(
                s, sess=sess, user_id=uid, text="hi", external_message_id="m1"
            )
            assert first.run_id == again.run_id  # uuid5(message_id) → same admission
            run = await s.get(Run, (tid, first.run_id))
            assert run is not None and run.status == "queued"
        finally:
            await s.rollback()


async def _seed_assistant_reply(
    s: AsyncSession, *, tid: uuid.UUID, uid: uuid.UUID, text_out: str
) -> tuple[SessionModel, uuid.UUID]:
    sess = await ensure_channel_session(
        s, tenant_id=tid, user_id=uid, channel="qq", installation_id="bot", external_id="777"
    )
    rid = uuid.uuid4()
    s.add(Run(tenant_id=tid, id=rid, session_id=sess.id, run_kind="web_chat", prompt_version="v1"))
    await s.flush()
    mid = uuid.uuid4()
    s.add(
        Message(
            tenant_id=tid,
            id=mid,
            session_id=sess.id,
            run_id=rid,
            author_user_id=None,
            seq=1,
            role="assistant",
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
            content_redacted={"text": text_out},
        )
    )
    await s.flush()
    return sess, rid


@pytest.mark.asyncio
async def test_deliver_run_reply_records_assistant_text() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner_like(s)
            sess, rid = await _seed_assistant_reply(s, tid=tid, uid=uid, text_out="Done: 42.")
            client = RecordingQQClient()
            ok = await deliver_run_reply(
                s, client=client, tenant_id=tid, run_id=rid, external_id=sess.external_scope_id
            )
            assert ok
            assert len(client.sent) == 1
            assert client.sent[0].to == "777"
            assert client.sent[0].text == "Done: 42."
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_deliver_run_reply_appends_pending_approval() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner_like(s)
            sess, rid = await _seed_assistant_reply(s, tid=tid, uid=uid, text_out="I'll send it.")
            args: dict[str, object] = {"to": "a@b.co", "subject": "Hi", "body": "Hello there"}
            effect_class = classify_effect(SendEmailTool().flags)
            handle = await begin_invocation(
                s,
                tenant_id=tid,
                run_id=rid,
                effect_name="send_email",
                idempotency_key=f"tool:{rid}:1:c1",
                effect_class=effect_class,
                retry_policy="transient_before_dispatch",
                args=args,
                turn_seq=1,
            )
            await request_approval(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sess.id,
                invocation_id=handle.invocation_id,
                tool_name="send_email",
                effect_class=effect_class,
                args=args,
                decider_user_id=uid,
            )
            client = RecordingQQClient()
            await deliver_run_reply(
                s, client=client, tenant_id=tid, run_id=rid, external_id=sess.external_scope_id
            )
            body = client.sent[0].text
            assert "I'll send it." in body
            assert "Approval needed for send_email" in body
            assert "approve" in body and "reject" in body
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_find_pending_approval_by_prefix_and_latest() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner_like(s)
            sess, rid = await _seed_assistant_reply(s, tid=tid, uid=uid, text_out="x")
            args: dict[str, object] = {"to": "a@b.co", "subject": "H", "body": "Hello there"}
            effect_class = classify_effect(SendEmailTool().flags)
            handle = await begin_invocation(
                s,
                tenant_id=tid,
                run_id=rid,
                effect_name="send_email",
                idempotency_key=f"tool:{rid}:1:c1",
                effect_class=effect_class,
                retry_policy="transient_before_dispatch",
                args=args,
                turn_seq=1,
            )
            created = await request_approval(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sess.id,
                invocation_id=handle.invocation_id,
                tool_name="send_email",
                effect_class=effect_class,
                args=args,
                decider_user_id=uid,
            )
            cid = created.envelope.correlation_id
            prefix = str(cid).replace("-", "")[:8]
            by_prefix = await find_pending_approval(
                s, tenant_id=tid, session_id=sess.id, correlation_prefix=prefix
            )
            assert by_prefix is not None and by_prefix.correlation_id == cid
            latest = await find_pending_approval(
                s, tenant_id=tid, session_id=sess.id, correlation_prefix=None
            )
            assert latest is not None and latest.correlation_id == cid
            miss = await find_pending_approval(
                s, tenant_id=tid, session_id=sess.id, correlation_prefix="ffffffff"
            )
            assert miss is None
        finally:
            await s.rollback()


# --------------------------------------------------------------------------- #
# API — webhook auth/routing + simulate + status (skip without DB + Redis).    #
# --------------------------------------------------------------------------- #


async def _drop_owner() -> None:
    tenant_id, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tenant_id})
        await s.commit()


@pytest.mark.asyncio
async def test_qq_simulate_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    enqueued: list[uuid.UUID] = []

    async def _fake_enqueue(run_id: uuid.UUID) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.channels.inbound.enqueue_run", _fake_enqueue)
    monkeypatch.setattr("app.api.channels.build_qq_client", RecordingQQClient)

    await _drop_owner()
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            assert r.status_code == 200
            csrf = r.json()["csrf_token"]

            # Seed a QQ config in the DB (official flow: config is DB-backed, not env).
            from app.auth import owner_ids as _oids

            tid, uid = _oids()
            async with SessionLocal() as s:
                await chan_svc.set_qq_config(
                    s,
                    tid,
                    uid,
                    app_id="102000001",
                    enabled=True,
                    owner_external_id="owner_openid",
                    secret="app-secret-xyz",
                )
                await s.commit()

            r = await client.post(
                "/channels/qq/simulate",
                json={"text": "what can you do?"},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200
            sim = r.json()
            assert sim["status"] == "queued"
            assert len(enqueued) == 1
            session_id = sim["session_id"]

            # status reflects DB config + shows the thread
            r = await client.get("/channels")
            assert r.status_code == 200
            st = r.json()
            assert st["qq"]["enabled"] and st["qq"]["configured"]
            assert st["qq"]["app_id"] == "102000001" and st["qq"]["secret_set"]
            assert any(
                t["session_id"] == session_id and t["channel"] == "qq" for t in st["threads"]
            )

            # thread transcript shows the inbound user message
            r = await client.get(f"/channels/threads/{session_id}")
            assert r.status_code == 200
            tx = r.json()
            assert tx["channel"] == "qq"
            assert any(
                m["role"] == "user" and "what can you do" in m["text"] for m in tx["messages"]
            )
    finally:
        await _drop_owner()
