"""Worker integration (#8-#10): an admitted prompt, run through the worker job,
executes the loop on the default mock provider and settles succeeded.

Integration test — skips without a database; commits and cleans up the tenant.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admission import admit_prompt
from app.db import SessionLocal, ping_db
from app.models import EventJournal, Message, Run, Tenant, User
from app.models import Session as SessionModel
from app.worker import run_job
from tests.db_guard import drop_tenant


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


async def _drop(tid: uuid.UUID) -> None:
    await drop_tenant(tid)


@pytest.mark.asyncio
async def test_run_job_executes_admitted_prompt() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        tid, uid, sid = await _seed(s)
        adm = await admit_prompt(
            s, tenant_id=tid, session_id=sid, user_id=uid, client_message_id=uuid.uuid4(), text="hi"
        )
        await s.commit()
    try:
        reason = await run_job({}, str(adm.run_id))
        assert reason == "completed"
        async with SessionLocal() as s:
            run = await s.get(Run, (tid, adm.run_id))
            assert run is not None and run.status == "succeeded" and run.settled_at is not None

            assistant = (
                (
                    await s.execute(
                        select(Message).where(
                            Message.tenant_id == tid,
                            Message.run_id == adm.run_id,
                            Message.role == "assistant",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(assistant) == 1

            types = set(
                (
                    await s.execute(
                        select(EventJournal.event_type).where(
                            EventJournal.tenant_id == tid, EventJournal.run_id == adm.run_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {"run.started", "turn.end", "run.settled"} <= types
    finally:
        await _drop(tid)
