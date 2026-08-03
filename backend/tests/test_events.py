"""append_event: journal + outbox in one transaction, monotonic per-run/session seq.

Integration test — skips when no database is reachable. Rolls back (no rows persisted).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db import SessionLocal, ping_db
from app.events import append_event
from app.events.stream import transient_sse_format
from app.models import EventJournal, Outbox, Run, Tenant, User
from app.models import Session as SessionModel


@pytest.mark.asyncio
async def test_append_event_orders_and_outboxes() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")

    tid, uid, sid, rid = (uuid.uuid4() for _ in range(4))

    async with SessionLocal() as s:
        try:
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
                )
            )
            await s.flush()

            first = await append_event(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sid,
                event_type="run.started",
                payload={"step": 1},
            )
            second = await append_event(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sid,
                event_type="turn.end",
                payload={"step": 2},
            )

            assert (first.run_seq, first.session_seq) == (1, 1)
            assert (second.run_seq, second.session_seq) == (2, 2)
            assert first.topic == f"session:{sid}"

            n_events = await s.scalar(
                select(func.count()).select_from(EventJournal).where(EventJournal.tenant_id == tid)
            )
            n_outbox = await s.scalar(
                select(func.count()).select_from(Outbox).where(Outbox.tenant_id == tid)
            )
            assert n_events == 2
            assert n_outbox == 2
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_append_event_serializes_concurrent_sequence_allocation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")

    tid, uid, sid, rid = (uuid.uuid4() for _ in range(4))
    async with SessionLocal() as s:
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
            )
        )
        await s.commit()

    async def write(event_type: str) -> tuple[int, int | None]:
        async with SessionLocal() as s:
            event = await append_event(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sid,
                event_type=event_type,
                payload={},
            )
            await s.commit()
            return event.run_seq, event.session_seq

    try:
        allocated = await asyncio.gather(write("tool-result"), write("tool-error"))
        assert sorted(seq for seq, _session_seq in allocated) == [1, 2]
        assert sorted(int(session_seq or 0) for _seq, session_seq in allocated) == [1, 2]
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Tenant).where(Tenant.tenant_id == tid))
            await s.commit()


def test_transient_sse_frame_has_no_replay_cursor() -> None:
    frame = transient_sse_format({"type": "runtime.output", "payload": {"delta": "ok"}})
    assert frame.startswith("event: runtime.output\n")
    assert "\nid:" not in frame
    assert '"delta":"ok"' in frame
