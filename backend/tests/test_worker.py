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
from app.worker import WorkerSettings, project_workcopy_maintenance, run_job
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


@pytest.mark.asyncio
async def test_project_maintenance_protects_live_runtime_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from app import worker
    from app.sandbox import runtime as sandbox_runtime
    from app.services import project_runtime as runtime_svc
    from app.services import project_sandbox as sandbox_svc
    from app.services import project_workcopy as workcopy_svc

    async def leader(*args, **kwargs) -> bool:  # type: ignore[no-untyped-def]
        return True

    async def expire_idle(session) -> int:  # type: ignore[no-untyped-def]
        return 2

    async def recover(session):  # type: ignore[no-untyped-def]
        return 1, ["expired-container"]

    async def protected(session):  # type: ignore[no-untyped-def]
        return frozenset({"live-container"})

    removed: list[str] = []
    swept: list[frozenset[str]] = []

    async def remove(ref):  # type: ignore[no-untyped-def]
        removed.append(ref)

    def sweep(*, protected_ids=frozenset()):  # type: ignore[no-untyped-def]
        swept.append(protected_ids)
        return 3

    monkeypatch.setattr(worker, "try_acquire_leader", leader)
    monkeypatch.setattr(workcopy_svc, "expire_idle", expire_idle)
    monkeypatch.setattr(runtime_svc, "recover_expired", recover)
    monkeypatch.setattr(runtime_svc, "protected_container_refs", protected)
    monkeypatch.setattr(sandbox_runtime, "remove_runtime_container", remove)
    monkeypatch.setattr(sandbox_svc, "sweep_orphan_scratch", sweep)

    result = await project_workcopy_maintenance({})
    assert removed == ["expired-container"]
    assert swept == [frozenset({"live-container"})]
    assert result == "expired=2 runtimes_recovered=1 containers_swept=3"


def test_runtime_jobs_disable_arq_retries_and_cover_product_timeout() -> None:
    wrapped = {
        item.name: item
        for item in WorkerSettings.functions
        if item.__class__.__name__ == "Function"
    }
    assert wrapped["project_runtime_open_job"].timeout_s == 900
    assert wrapped["project_runtime_exec_job"].timeout_s == 1300
    assert wrapped["project_runtime_close_job"].timeout_s == 900
    for name in (
        "project_runtime_open_job",
        "project_runtime_exec_job",
        "project_runtime_close_job",
    ):
        assert wrapped[name].max_tries == 1
