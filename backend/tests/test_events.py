"""append_event: journal + outbox in one transaction, monotonic per-run/session seq.

Integration test — skips when no database is reachable. Rolls back (no rows persisted).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal, ping_db
from app.events import append_event
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
