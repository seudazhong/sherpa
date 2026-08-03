"""Permission engine (m2-20): the gate + first-valid-response-wins resolution.

Two integration tests (skip without Postgres):

* the core loop *gates* a non-read-only tool (``send_email``): it creates a pending
  approval envelope + emits ``permission.asked`` and does NOT execute the effect;
* the ``POST /permissions/{id}/resolve`` endpoint enforces first-valid-response-wins
  (winner decides; different later submission -> 409; exact retry -> idempotent 200),
  actor authorization (-> 403), and expiry (-> 410).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ensure_owner, owner_ids
from app.config import settings
from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.effects import args_hash, begin_invocation
from app.main import app
from app.models import ApprovalEnvelope, EffectInvocation, EventJournal, Message, Run, Tenant, User
from app.models import Session as SessionModel
from app.permissions import request_approval
from app.permissions.service import build_preview
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.redis_client import ping_redis
from app.tools import build_default_registry
from tests.db_guard import drop_owner_tenant

_ARGS: dict[str, object] = {"to": "a@b.com", "subject": "Hi", "body": "Hello there"}


def test_sensitive_fs_and_shell_previews_exclude_file_contents() -> None:
    fs = build_preview(
        "fs_write",
        {"path": ".env", "content": "SECRET=do-not-persist", "if_hash": "0" * 64},
    )
    assert "SECRET" not in str(fs)
    assert ".env" in str(fs)
    sh = build_preview(
        "sh_exec",
        {"runtime_session_id": uuid.uuid4(), "command": "rm -rf /work/tmp"},
    )
    assert "rm -rf /work/tmp" in str(sh)


async def _drop_owner() -> None:
    await drop_owner_tenant()


async def _seed_session_run(
    s: AsyncSession, tid: uuid.UUID, uid: uuid.UUID
) -> tuple[uuid.UUID, Run]:
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
    return sid, run


@pytest.mark.asyncio
async def test_loop_gates_send_email_without_executing() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid = uuid.uuid4()
            uid = uuid.uuid4()
            s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
            await s.flush()
            s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
            await s.flush()
            _sid, run = await _seed_session_run(s, tid, uid)

            provider = MockProvider(
                script=[
                    [
                        ToolCall(id="c1", name="email_send", args=_ARGS),
                        Finish("tool_use"),
                    ],
                    [TextDelta("I have requested your approval to send it."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "awaiting_approval"
            assert run.status == "running"
            assert run.settled_at is None

            # A pending envelope is bound to this run and its invocation.
            env = (
                await s.execute(
                    select(ApprovalEnvelope).where(
                        ApprovalEnvelope.tenant_id == tid, ApprovalEnvelope.run_id == run.id
                    )
                )
            ).scalar_one()
            assert env.status == "pending"
            assert env.tool_name == "email_send"
            assert env.effect_class == "non_idempotent_write"
            assert env.permission_scope == "tool:email_send"
            assert env.args_hash == args_hash(_ARGS)
            assert env.authorized_decider_user_id == uid

            # permission.asked event was emitted; the effect was NOT executed.
            types = set(
                (
                    await s.execute(
                        select(EventJournal.event_type).where(
                            EventJournal.tenant_id == tid, EventJournal.run_id == run.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert "permission.asked" in types
            assert "run.settled" not in types
            assistant_count = await s.scalar(
                select(text("count(*)"))
                .select_from(Message)
                .where(
                    Message.tenant_id == tid,
                    Message.run_id == run.id,
                    Message.role == "assistant",
                )
            )
            assert assistant_count == 0

            inv = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid,
                        EffectInvocation.invocation_id == env.invocation_id,
                    )
                )
            ).scalar_one()
            assert inv.effect_name == "email_send"
            assert inv.status == "prepared"  # never dispatched
            assert inv.outcome is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_resolve_web_without_nonce() -> None:
    # ADR-034: a background/scheduled approval has no live SSE to carry the nonce;
    # web owner resolution succeeds without it (session+CSRF+actor+binding).
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            login = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}

            # A background/scheduled approval: resolve from the list WITHOUT the nonce.
            d = await _seed_owner_envelope()
            body = _envelope_body(
                d["env"],
                session_id=d["session_id"],
                nonce=None,
                choice="allow_once",
                actor_id=d["actor_id"],
            )
            ok = await client.post(
                f"/permissions/{d['correlation_id']}/resolve", json=body, headers=headers
            )
            assert ok.status_code == 200
            assert ok.json()["winning_decision"]["choice"] == "allow_once"

            # A WRONG nonce is still rejected (verified when supplied) -> 422.
            d2 = await _seed_owner_envelope()
            bad = _envelope_body(
                d2["env"],
                session_id=d2["session_id"],
                nonce="A" * 43,
                choice="allow_once",
                actor_id=d2["actor_id"],
            )
            r = await client.post(
                f"/permissions/{d2['correlation_id']}/resolve", json=bad, headers=headers
            )
            assert r.status_code == 422
    finally:
        await _drop_owner()


def _envelope_body(
    env: ApprovalEnvelope,
    *,
    session_id: uuid.UUID,
    nonce: str | None,
    choice: str,
    actor_id: uuid.UUID,
    channel: str = "web",
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "1.0",
        "correlation_id": str(env.correlation_id),
        "bound": {
            "tenant_id": str(env.tenant_id),
            "run_id": str(env.run_id),
            "invocation_id": str(env.invocation_id),
        },
        "action": {
            "tool_name": env.tool_name,
            "permission_scope": env.permission_scope,
            "session_id": str(session_id),
        },
        "effect_class": env.effect_class,
        "normalized_args_hash": env.args_hash.hex(),
        "human_readable_preview": env.preview_redacted,
        "policy_version": env.policy_version,
        "expires_at": env.expires_at.isoformat(),
        "authorized_actor": {"type": "user", "id": str(actor_id)},
        "decision": {
            "actor": {"type": "user", "id": str(actor_id)},
            "channel": channel,
            "choice": choice,
        },
    }
    if nonce is not None:
        body["nonce"] = nonce
    return body


async def _seed_owner_envelope(ttl_seconds: int = 3600) -> dict[str, object]:
    """Create a pending send_email envelope under the owner tenant; return its data."""
    async with SessionLocal() as s:
        tid, uid = await ensure_owner(s)
        sid, run = await _seed_session_run(s, tid, uid)
        handle = await begin_invocation(
            s,
            tenant_id=tid,
            run_id=run.id,
            effect_name="email_send",
            idempotency_key=f"tool:{run.id}:1:{uuid.uuid4()}",
            effect_class="non_idempotent_write",
            retry_policy="transient_before_dispatch",
            args=_ARGS,
            turn_seq=1,
        )
        created = await request_approval(
            s,
            tenant_id=tid,
            run_id=run.id,
            session_id=sid,
            invocation_id=handle.invocation_id,
            tool_name="email_send",
            effect_class="non_idempotent_write",
            args=_ARGS,
            decider_user_id=uid,
            ttl_seconds=ttl_seconds,
        )
        env = created.envelope
        data = {
            "env": env,
            "nonce": created.nonce,
            "session_id": sid,
            "correlation_id": env.correlation_id,
            "actor_id": uid,
        }
        await s.commit()
        return data


@pytest.mark.asyncio
async def test_resolve_first_valid_wins_and_authz() -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            login = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}

            d = await _seed_owner_envelope()
            env, nonce, sid, corr, actor = (
                d["env"],
                str(d["nonce"]),
                d["session_id"],
                d["correlation_id"],
                d["actor_id"],
            )

            # GET /permissions surfaces the pending ask (no nonce leaked).
            listing = await client.get("/permissions")
            assert listing.status_code == 200
            items = listing.json()["items"]
            assert any(i["correlation_id"] == str(corr) for i in items)
            assert "nonce" not in items[0]

            body = _envelope_body(
                env, session_id=sid, nonce=nonce, choice="allow_once", actor_id=actor
            )

            # Winner resolves the pending envelope.
            win = await client.post(f"/permissions/{corr}/resolve", json=body, headers=headers)
            assert win.status_code == 200
            assert win.json()["state"] == "resolved"
            assert win.json()["winning_decision"]["choice"] == "allow_once"

            # A different later submission loses -> 409.
            reject = _envelope_body(
                env, session_id=sid, nonce=nonce, choice="reject", actor_id=actor
            )
            lose = await client.post(f"/permissions/{corr}/resolve", json=reject, headers=headers)
            assert lose.status_code == 409

            # Exact retry of the winning submission is idempotent -> 200.
            retry = await client.post(f"/permissions/{corr}/resolve", json=body, headers=headers)
            assert retry.status_code == 200
            assert retry.json()["winning_decision"]["choice"] == "allow_once"

            # No longer pending.
            listing2 = await client.get("/permissions")
            assert not any(i["correlation_id"] == str(corr) for i in listing2.json()["items"])

            # Each decision mode transitions a fresh pending envelope.
            for choice in ("allow_session", "always", "reject"):
                d2 = await _seed_owner_envelope()
                b2 = _envelope_body(
                    d2["env"],
                    session_id=d2["session_id"],
                    nonce=str(d2["nonce"]),
                    choice=choice,
                    actor_id=d2["actor_id"],
                )
                r2 = await client.post(
                    f"/permissions/{d2['correlation_id']}/resolve", json=b2, headers=headers
                )
                assert r2.status_code == 200, choice
                assert r2.json()["winning_decision"]["choice"] == choice

            # Actor mismatch: a non-authorized actor cannot resolve -> 403.
            d3 = await _seed_owner_envelope()
            other = uuid.uuid4()
            b3 = _envelope_body(
                d3["env"],
                session_id=d3["session_id"],
                nonce=str(d3["nonce"]),
                choice="allow_once",
                actor_id=other,
            )
            forb = await client.post(
                f"/permissions/{d3['correlation_id']}/resolve", json=b3, headers=headers
            )
            assert forb.status_code == 403

            # Bad nonce -> 422 (binding mismatch, does not reveal the envelope).
            b4 = _envelope_body(
                d3["env"],
                session_id=d3["session_id"],
                nonce="A" * 43,
                choice="allow_once",
                actor_id=d3["actor_id"],
            )
            bad = await client.post(
                f"/permissions/{d3['correlation_id']}/resolve", json=b4, headers=headers
            )
            assert bad.status_code == 422

            # Expiry -> 410; no late response resolves it.
            d5 = await _seed_owner_envelope()
            async with SessionLocal() as s:
                await s.execute(
                    text(
                        "UPDATE approval_envelopes SET requested_at = now() - interval '2 hours', "
                        "expires_at = now() - interval '1 hour' WHERE tenant_id = :t AND id = :i"
                    ),
                    {"t": owner_ids()[0], "i": d5["env"].id},
                )
                await s.commit()
            b5 = _envelope_body(
                d5["env"],
                session_id=d5["session_id"],
                nonce=str(d5["nonce"]),
                choice="allow_once",
                actor_id=d5["actor_id"],
            )
            expired = await client.post(
                f"/permissions/{d5['correlation_id']}/resolve", json=b5, headers=headers
            )
            assert expired.status_code == 410
    finally:
        await _drop_owner()
