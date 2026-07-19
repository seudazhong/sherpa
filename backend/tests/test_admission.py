"""Durable prompt admission (#9): DB-level admission/idempotency + HTTP endpoint.

Integration test — skips when no database is reachable. These tests COMMIT (to
exercise the deferred admitted_seq pointer FKs) and clean up by deleting the
tenant (cascades across the spine).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admission import PromptConflict, admit_prompt
from app.db import SessionLocal, ping_db
from app.main import app
from app.models import Message, Part, Run, Tenant, User
from app.models import Session as SessionModel


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tid, uid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
    return tid, uid, sid


async def _drop_tenant(tid: uuid.UUID) -> None:
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


@pytest.mark.asyncio
async def test_admit_persists_queued_run_and_is_idempotent() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        tid, uid, sid = await _seed(s)
        cmid = uuid.uuid4()
        adm = await admit_prompt(
            s, tenant_id=tid, session_id=sid, user_id=uid, client_message_id=cmid, text="hi"
        )
        await s.commit()
    try:
        async with SessionLocal() as s:
            run = (
                await s.execute(select(Run).where(Run.tenant_id == tid, Run.id == adm.run_id))
            ).scalar_one()
            assert run.status == "queued"
            assert run.admitted_seq == adm.admitted_seq
            assert run.run_kind == "web_chat"

            msg = (
                await s.execute(
                    select(Message).where(Message.tenant_id == tid, Message.id == adm.message_id)
                )
            ).scalar_one()
            assert msg.role == "user" and msg.client_message_id == cmid
            assert msg.seq == adm.admitted_seq

            part_text = await s.scalar(
                select(Part.content_redacted).where(
                    Part.tenant_id == tid, Part.message_id == adm.message_id
                )
            )
            assert part_text == {"text": "hi"}

            sess = await s.get(SessionModel, (tid, sid))
            assert sess is not None and sess.admitted_seq == adm.admitted_seq

        # same client_message_id + same body -> original admission, no new message
        async with SessionLocal() as s:
            again = await admit_prompt(
                s, tenant_id=tid, session_id=sid, user_id=uid, client_message_id=cmid, text="hi"
            )
            await s.commit()
            assert again.reused is True and again.run_id == adm.run_id
            count = await s.scalar(
                select(func.count()).select_from(Message).where(Message.tenant_id == tid)
            )
            assert count == 1

        # same client_message_id + different body -> conflict
        async with SessionLocal() as s:
            with pytest.raises(PromptConflict):
                await admit_prompt(
                    s, tenant_id=tid, session_id=sid, user_id=uid, client_message_id=cmid, text="X"
                )
            await s.rollback()
    finally:
        await _drop_tenant(tid)


@pytest.mark.asyncio
async def test_prompt_endpoint_returns_202(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")

    enqueued: list[uuid.UUID] = []

    async def _fake_enqueue(run_id: uuid.UUID) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.api.prompt.queue.enqueue_run", _fake_enqueue)

    async with SessionLocal() as s:
        tid, _uid, sid = await _seed(s)
        await s.commit()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                f"/sessions/{sid}/prompt",
                json={"client_message_id": str(uuid.uuid4()), "text": "hello"},
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["state"] == "queued"
        assert body["session_id"] == str(sid)
        assert body["events_url"].startswith(f"/sessions/{sid}/events?cursor=")
        assert len(enqueued) == 1 and str(enqueued[0]) == body["run_id"]
    finally:
        await _drop_tenant(tid)
