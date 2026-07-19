"""Append-only event journal writer (ADR-016).

`append_event` writes one immutable journal row and one transactional outbox row
inside the caller's transaction (the "one PostgreSQL transaction" of the required
path in contracts/events-and-effects.md §3.1). It does NOT commit — the caller owns
the transaction boundary so the business mutation, event, and outbox commit atomically.

`run_seq` (and `session_seq` when session-scoped) are assigned server-side as
MAX(seq)+1 within the run/session. v1 is session-serial (one run per session at a
time), so this is safe; a future concurrent design would add explicit sequencing.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_INSERT_EVENT = text("""
    INSERT INTO event_journal (
        tenant_id, id, session_id, session_seq, run_id, run_seq, event_type,
        envelope_version, durability, correlation_id, causation_event_id,
        payload_redacted, payload_size_bytes, occurred_at
    ) VALUES (
        :tenant_id, :id, :session_id,
        CASE WHEN CAST(:session_id AS uuid) IS NULL THEN NULL
             ELSE (SELECT COALESCE(MAX(session_seq), 0) + 1 FROM event_journal
                   WHERE tenant_id = :tenant_id AND session_id = :session_id) END,
        :run_id,
        (SELECT COALESCE(MAX(run_seq), 0) + 1 FROM event_journal
         WHERE tenant_id = :tenant_id AND run_id = :run_id),
        :event_type, :envelope_version, :durability, :correlation_id, :causation_event_id,
        CAST(:payload AS jsonb), octet_length(CAST(:payload AS jsonb)::text), :occurred_at
    )
    RETURNING id, run_seq, session_seq
""")

_INSERT_OUTBOX = text("""
    INSERT INTO outbox (tenant_id, id, event_id, topic, delivery_key)
    VALUES (:tenant_id, :id, :event_id, :topic, :delivery_key)
""")


@dataclasses.dataclass(frozen=True)
class AppendedEvent:
    event_id: uuid.UUID
    run_seq: int
    session_seq: int | None
    topic: str


async def append_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict[str, object],
    session_id: uuid.UUID | None = None,
    envelope_version: int = 1,
    durability: str = "durable",
    correlation_id: uuid.UUID | None = None,
    causation_event_id: uuid.UUID | None = None,
    topic: str | None = None,
    occurred_at: datetime.datetime | None = None,
) -> AppendedEvent:
    """Append one journal row + one outbox row in the caller's transaction."""
    event_id = uuid.uuid4()
    occurred = occurred_at or datetime.datetime.now(datetime.UTC)
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if topic is None:
        topic = f"session:{session_id}" if session_id is not None else f"run:{run_id}"

    row = (
        await session.execute(
            _INSERT_EVENT,
            {
                "tenant_id": tenant_id,
                "id": event_id,
                "session_id": session_id,
                "run_id": run_id,
                "event_type": event_type,
                "envelope_version": envelope_version,
                "durability": durability,
                "correlation_id": correlation_id,
                "causation_event_id": causation_event_id,
                "payload": payload_json,
                "occurred_at": occurred,
            },
        )
    ).one()

    await session.execute(
        _INSERT_OUTBOX,
        {
            "tenant_id": tenant_id,
            "id": uuid.uuid4(),
            "event_id": row.id,
            "topic": topic,
            "delivery_key": str(row.id),
        },
    )

    return AppendedEvent(
        event_id=row.id, run_seq=row.run_seq, session_seq=row.session_seq, topic=topic
    )
