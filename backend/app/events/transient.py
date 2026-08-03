"""Best-effort session events for high-frequency runtime presentation updates.

Runtime stdout/stderr is not recovery truth; the durable truth is the exec row plus the
persisted working-copy boundary. These frames therefore bypass the journal/outbox and use
the existing Redis session stream only. They carry no SSE cursor and are intentionally
lost on disconnect.
"""

from __future__ import annotations

import datetime
import json
import uuid

from app.events.stream import stream_key
from app.redis_client import client as redis


async def publish_transient_session_event(
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    event_type: str,
    payload: dict[str, object],
    run_id: uuid.UUID | None = None,
) -> None:
    envelope: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "session_id": str(session_id),
        "run_id": str(run_id) if run_id is not None else None,
        "type": event_type,
        "durability": "debug",
        "occurred_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "payload": payload,
    }
    await redis.xadd(
        stream_key(tenant_id, session_id),
        {"transient": json.dumps(envelope, separators=(",", ":"))},
        maxlen=2000,
        approximate=True,
    )
