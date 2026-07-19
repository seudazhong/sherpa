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
from app.models import Session as SessionModel
from app.models import Tenant, Trace, User
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
