"""Outbox relay: publish pending journal events to per-session Redis Streams.

At-least-once (ADR-016): claims pending outbox rows (SKIP LOCKED), XADDs the
envelope to the session stream, marks them delivered. Idempotent for consumers
via event_id / session_seq dedup. Redis pub/sub is never used.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.envelope import to_envelope
from app.events.stream import stream_key
from app.models import EventJournal, Outbox


async def relay_once(session: AsyncSession, redis: Any, limit: int = 100) -> int:
    """Publish up to `limit` pending outbox rows; returns how many were relayed."""
    stmt = (
        select(EventJournal, Outbox)
        .join(
            Outbox,
            and_(Outbox.tenant_id == EventJournal.tenant_id, Outbox.event_id == EventJournal.id),
        )
        .where(Outbox.status == "pending")
        .order_by(Outbox.available_at, Outbox.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Outbox)
    )
    rows = (await session.execute(stmt)).all()
    now = datetime.datetime.now(datetime.UTC)
    for event, outbox in rows:
        if event.session_id is not None:
            key = stream_key(event.tenant_id, event.session_id)
            payload = json.dumps(to_envelope(event), separators=(",", ":"))
            await redis.xadd(key, {"envelope": payload})
        outbox.status = "delivered"
        outbox.delivered_at = now
    await session.flush()
    return len(rows)
