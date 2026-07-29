"""Agentic email (milestone 5, ADR-027).

Unit tests (no network): Svix verify, recording client, the unified send seam, and
the send_email tool routing through it. API tests (skip without Postgres+Redis):
email webhook auth/routing, simulate/status/transcript — enqueue is monkeypatched
and the mailbox client is a recorder, so tests never touch AgentMail or Redis-jobs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from httpx import ASGITransport

from app.channels.email import (
    AgentMailClient,
    RecordingEmailClient,
    build_email_channel_client,
    verify_svix_signature,
)
from app.config import settings
from app.db import ping_db
from app.main import app
from app.notifications import build_email_sender
from app.notifications.email import AgentMailEmailSender, RecordingEmailSender
from app.redis_client import ping_redis
from app.tools import ToolContext
from app.tools.builtin import SendEmailTool
from tests.db_guard import drop_owner_tenant

# --------------------------------------------------------------------------- #
# Unit — no I/O.                                                               #
# --------------------------------------------------------------------------- #


def _svix_sign(secret_b64: str, svix_id: str, ts: str, body: bytes) -> str:
    key = base64.b64decode(secret_b64)
    signed = f"{svix_id}.{ts}.".encode() + body
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def test_verify_svix_signature_roundtrip() -> None:
    raw_secret = base64.b64encode(b"supersecretkeybytes!").decode()
    secret = f"whsec_{raw_secret}"
    body = b'{"event_type":"message.received"}'
    sid, ts = "msg_1", "1700000000"
    good = _svix_sign(raw_secret, sid, ts, body)
    assert verify_svix_signature(secret, body, svix_id=sid, svix_timestamp=ts, svix_signature=good)
    # tampered body
    assert not verify_svix_signature(
        secret, b"tampered", svix_id=sid, svix_timestamp=ts, svix_signature=good
    )
    # missing headers
    assert not verify_svix_signature(
        secret, body, svix_id=None, svix_timestamp=ts, svix_signature=good
    )
    # empty secret = trusted/unsigned mode → accept
    assert verify_svix_signature("", body, svix_id=None, svix_timestamp=None, svix_signature=None)


@pytest.mark.asyncio
async def test_recording_email_client_records() -> None:
    client = RecordingEmailClient()
    mid = await client.send(to="a@b.co", subject="Hi", text="Hello there")
    assert mid == "recorded"
    assert client.sent[0].to == "a@b.co"


@pytest.mark.asyncio
async def test_agentmail_client_without_key_returns_none() -> None:
    client = AgentMailClient(api_base="https://api.agentmail.to", api_key="", inbox_id="")
    assert await client.send(to="a@b.co", subject="Hi", text="Hello") is None


def test_build_email_channel_client_default_is_recording() -> None:
    assert isinstance(build_email_channel_client(), RecordingEmailClient)


def test_build_email_sender_default_and_agentmail(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(build_email_sender(), RecordingEmailSender)
    monkeypatch.setattr(settings, "email_kind", "agentmail")
    assert isinstance(build_email_sender(), AgentMailEmailSender)


@pytest.mark.asyncio
async def test_send_email_tool_routes_through_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    """The send_email tool goes through the single build_email_sender() seam."""
    sender = RecordingEmailSender()
    monkeypatch.setattr("app.notifications.build_email_sender", lambda: sender)
    ctx = ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        session=None,  # type: ignore[arg-type]  # execute() does not touch the session
    )
    result = await SendEmailTool().execute(
        ctx, {"to": "x@y.co", "subject": "Report", "body": "It is ready."}
    )
    assert "email sent to x@y.co" in result.llm_content
    assert len(sender.sent) == 1
    assert sender.sent[0].to == "x@y.co"
    assert sender.sent[0].subject == "Report"


# --------------------------------------------------------------------------- #
# API — email webhook auth/routing + simulate/status/transcript.              #
# --------------------------------------------------------------------------- #


async def _drop_owner() -> None:
    await drop_owner_tenant()


_RAW_SECRET = base64.b64encode(b"emailhooksecretbytes").decode()


def _configure_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "email_kind", "agentmail")
    monkeypatch.setattr(settings, "agentmail_inbox_id", "cloudysample676@agentmail.to")
    monkeypatch.setattr(settings, "agentmail_webhook_secret", f"whsec_{_RAW_SECRET}")
    monkeypatch.setattr(settings, "agentmail_owner_email", "boss@example.com")
    monkeypatch.setattr("app.api.channels.build_email_channel_client", RecordingEmailClient)


@pytest.mark.asyncio
async def test_email_webhook_auth_and_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    enqueued: list[uuid.UUID] = []

    async def _fake_enqueue(run_id: uuid.UUID) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.channels.inbound.enqueue_run", _fake_enqueue)
    _configure_email(monkeypatch)

    await _drop_owner()
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            body = json.dumps(
                {
                    "event_type": "message.received",
                    "message": {
                        "from": "Boss <boss@example.com>",
                        "subject": "Please summarize",
                        "text": "Can you summarize my open tasks?",
                        "message_id": "m-1",
                    },
                }
            ).encode()
            sid, ts = "msg_abc", "1700000001"

            # bad signature -> 401
            r = await client.post(
                "/channels/email/webhook",
                content=body,
                headers={"svix-id": sid, "svix-timestamp": ts, "svix-signature": "v1,bad"},
            )
            assert r.status_code == 401

            good = _svix_sign(_RAW_SECRET, sid, ts, body)
            headers = {"svix-id": sid, "svix-timestamp": ts, "svix-signature": good}

            # non-owner sender -> 403
            other = json.dumps(
                {
                    "event_type": "message.received",
                    "message": {"from": "stranger@evil.com", "text": "hi", "message_id": "m-2"},
                }
            ).encode()
            r = await client.post(
                "/channels/email/webhook",
                content=other,
                headers={
                    "svix-id": "msg_o",
                    "svix-timestamp": ts,
                    "svix-signature": _svix_sign(_RAW_SECRET, "msg_o", ts, other),
                },
            )
            assert r.status_code == 403

            # valid owner email -> queued + a run enqueued + an email session
            r = await client.post("/channels/email/webhook", content=body, headers=headers)
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "queued"
            assert len(enqueued) == 1
            assert uuid.UUID(data["session_id"])
    finally:
        await _drop_owner()


@pytest.mark.asyncio
async def test_email_simulate_status_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    enqueued: list[uuid.UUID] = []

    async def _fake_enqueue(run_id: uuid.UUID) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.channels.inbound.enqueue_run", _fake_enqueue)
    _configure_email(monkeypatch)

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

            r = await client.post(
                "/channels/email/simulate",
                json={"text": "What's on my plate today?"},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200
            sim = r.json()
            assert sim["status"] == "queued"
            assert len(enqueued) == 1
            session_id = sim["session_id"]

            r = await client.get("/channels")
            assert r.status_code == 200
            st = r.json()
            assert st["email"]["enabled"] and st["email"]["configured"]
            assert st["email"]["inbox_id"] == "cloudysample676@agentmail.to"
            thread = next(t for t in st["threads"] if t["session_id"] == session_id)
            assert thread["channel"] == "email"

            r = await client.get(f"/channels/threads/{session_id}")
            assert r.status_code == 200
            tx = r.json()
            assert tx["channel"] == "email"
            assert any(m["role"] == "user" and "plate" in m["text"] for m in tx["messages"])
    finally:
        await _drop_owner()
