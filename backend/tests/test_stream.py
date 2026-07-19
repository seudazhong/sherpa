"""Event stream building blocks: SSE framing, journal backlog, outbox→Redis relay.

Integration tests (relay/backlog) skip when DB/Redis are unavailable; they run
inside a rolled-back transaction and clean up the Redis stream.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.events import append_event, read_backlog, relay_once, sse_format, stream_key
from app.models import Run, Tenant, User
from app.models import Session as SessionModel
from app.redis_client import client as redis
from app.redis_client import ping_redis


async def _seed(s) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    tid, uid, sid, rid = (uuid.uuid4() for _ in range(4))
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
    s.add(Run(tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1"))
    await s.flush()
    return tid, sid, rid


def test_sse_format() -> None:
    frame = sse_format({"session_seq": 42, "type": "text-delta", "payload": {"t": "hi"}})
    assert frame.startswith("id: 42\nevent: text-delta\ndata: ")
    assert frame.endswith("\n\n")


@pytest.mark.asyncio
async def test_read_backlog_orders_after_cursor() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, sid, rid = await _seed(s)
            await append_event(
                s, tenant_id=tid, run_id=rid, session_id=sid, event_type="a", payload={"n": 1}
            )
            await append_event(
                s, tenant_id=tid, run_id=rid, session_id=sid, event_type="b", payload={"n": 2}
            )
            assert len(await read_backlog(s, tid, sid, 0)) == 2
            tail = await read_backlog(s, tid, sid, 1)
            assert [e["type"] for e in tail] == ["b"]
            assert tail[0]["session_seq"] == 2
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_relay_publishes_to_stream() -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    async with SessionLocal() as s:
        tid = sid = None
        try:
            tid, sid, rid = await _seed(s)
            await append_event(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sid,
                event_type="run.started",
                payload={"n": 1},
            )
            await append_event(
                s,
                tenant_id=tid,
                run_id=rid,
                session_id=sid,
                event_type="turn.end",
                payload={"n": 2},
            )
            n = await relay_once(s, redis)
            assert n == 2
            assert await redis.xlen(stream_key(tid, sid)) == 2
        finally:
            if tid is not None and sid is not None:
                await redis.delete(stream_key(tid, sid))
            await s.rollback()
