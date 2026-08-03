"""Session event stream: journal backlog + live Redis Stream (ADR-016, §3).

The journal is the source of truth. On connect the server emits catch-up events
from PostgreSQL (session_seq > cursor), then tails the per-session Redis Stream,
deduplicating by session_seq. Redis is only an accelerator, never the sole source.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.events.envelope import to_envelope
from app.models import EventJournal
from app.redis_client import client as redis


def stream_key(tenant_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return f"sherpa:v1:events:{tenant_id}:{session_id}"


def sse_format(env: dict[str, object]) -> str:
    """One SSE frame: id = session_seq, event = type, data = compact envelope JSON."""
    data = json.dumps(env, separators=(",", ":"))
    return f"id: {env['session_seq']}\nevent: {env['type']}\ndata: {data}\n\n"


def transient_sse_format(env: dict[str, object]) -> str:
    """A best-effort frame has no public cursor and is never replayed."""
    data = json.dumps(env, separators=(",", ":"))
    return f"event: {env['type']}\ndata: {data}\n\n"


async def read_backlog(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    after_seq: int,
) -> list[dict[str, object]]:
    """Journaled events for the session with session_seq > after_seq, in order."""
    rows = (
        (
            await session.execute(
                select(EventJournal)
                .where(
                    EventJournal.tenant_id == tenant_id,
                    EventJournal.session_id == session_id,
                    EventJournal.session_seq > after_seq,
                )
                .order_by(EventJournal.session_seq)
            )
        )
        .scalars()
        .all()
    )
    return [to_envelope(r) for r in rows]


async def session_event_stream(
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    after_seq: int = 0,
    live: bool = True,
    block_ms: int = 15000,
) -> AsyncIterator[str]:
    """SSE body: emit journal catch-up first, then tail the Redis stream (deduped)."""
    last_seq = after_seq
    async with SessionLocal() as session:
        backlog = await read_backlog(session, tenant_id, session_id, after_seq)
    for env in backlog:
        seq = env["session_seq"]
        if isinstance(seq, int):
            last_seq = max(last_seq, seq)
        yield sse_format(env)

    if not live:
        return

    key = stream_key(tenant_id, session_id)
    last_id = "$"
    while True:
        result = await redis.xread({key: last_id}, block=block_ms, count=50)
        if not result:
            yield ": keep-alive\n\n"
            continue
        for _stream, entries in result:
            for entry_id, fields in entries:
                last_id = entry_id
                transient = fields.get("transient")
                if transient:
                    yield transient_sse_format(json.loads(transient))
                    continue
                raw = fields.get("envelope")
                if not raw:
                    continue
                env = json.loads(raw)
                seq = env.get("session_seq") or 0
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield sse_format(env)
