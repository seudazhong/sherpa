"""Observability (#12): run->trace projection + session rollups, and the
structured JSON formatter (redaction + correlation). Formatter test needs no DB.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admission import admit_prompt
from app.db import SessionLocal, ping_db
from app.models import Run, Tenant, Trace, User
from app.models import Session as SessionModel
from app.observability import JsonFormatter, tenant_id_var
from app.worker import run_job


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
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


@pytest.mark.asyncio
async def test_run_yields_trace_with_usage_and_rollups() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        tid, uid, sid = await _seed(s)
        adm = await admit_prompt(
            s,
            tenant_id=tid,
            session_id=sid,
            user_id=uid,
            client_message_id=uuid.uuid4(),
            text="hello world",
        )
        await s.commit()
    try:
        await run_job({}, str(adm.run_id))
        async with SessionLocal() as s:
            trace = (
                await s.execute(
                    select(Trace).where(Trace.tenant_id == tid, Trace.run_id == adm.run_id)
                )
            ).scalar_one()
            assert trace.status == "succeeded"
            assert trace.trace_kind == "web_chat"
            assert trace.ended_at is not None
            assert "model" in trace.tags
            assert int(trace.tags["output_tokens"]) > 0
            assert int(trace.tags["input_tokens"]) > 0

            sess = await s.get(SessionModel, (tid, sid))
            assert sess is not None
            assert sess.output_tokens_rollup > 0
            assert sess.input_tokens_rollup > 0
    finally:
        await _drop(tid)


@pytest.mark.asyncio
async def test_run_failure_journals_provider_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider HTTP failure settles the run failed AND records the real reason.

    Regression for "400 bad request, logs too terse": the worker used to swallow
    the exception. Now run.settled carries the ProviderError detail (status + the
    proxy's actual message) so the failure is diagnosable from the journal.
    """
    if not await ping_db():
        pytest.skip("database not reachable")
    from collections.abc import AsyncIterator

    from app import worker as worker_mod
    from app.models import EventJournal
    from app.providers import ProviderError, ProviderEvent

    class _FailingProvider:
        name = "openai_compatible"
        _model = "claude-sonnet-4.6"

        async def stream(self, **_: object) -> AsyncIterator[ProviderEvent]:
            raise ProviderError(
                "provider chat completion failed",
                status_code=400,
                body='{"error":{"message":"No connected db"}}',
            )
            yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(worker_mod, "build_provider", lambda: _FailingProvider())

    async with SessionLocal() as s:
        tid, uid, sid = await _seed(s)
        adm = await admit_prompt(
            s,
            tenant_id=tid,
            session_id=sid,
            user_id=uid,
            client_message_id=uuid.uuid4(),
            text="trigger a provider error",
        )
        await s.commit()
    try:
        result = await run_job({}, str(adm.run_id))
        assert result == "failed"
        async with SessionLocal() as s:
            run = await s.get(Run, (tid, adm.run_id))
            assert run is not None and run.status == "failed"
            settled = (
                (
                    await s.execute(
                        select(EventJournal).where(
                            EventJournal.tenant_id == tid,
                            EventJournal.run_id == adm.run_id,
                            EventJournal.event_type == "run.settled",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(settled) == 1
            error = settled[0].payload_redacted.get("error", "")
            assert "status=400" in error
            assert "No connected db" in error
    finally:
        await _drop(tid)


def test_json_formatter_redacts_and_correlates() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="sherpa.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="run %s done",
        args=("abc",),
        exc_info=None,
    )
    record.refresh_token = "super-secret-value"  # type: ignore[attr-defined]
    record.turns = 3  # type: ignore[attr-defined]

    token = tenant_id_var.set("tenant-123")
    try:
        out = json.loads(formatter.format(record))
    finally:
        tenant_id_var.reset(token)

    assert out["message"] == "run abc done"
    assert out["level"] == "INFO"
    assert out["tenant_id"] == "tenant-123"
    assert out["turns"] == 3
    assert out["refresh_token"] == "***REDACTED***"
