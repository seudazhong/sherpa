"""Build the frozen event envelope (contracts/events-and-effects.md §1) from a journal row."""

from __future__ import annotations

import datetime

from app.models import EventJournal

SCHEMA_VERSION = "1.0"


def to_envelope(row: EventJournal) -> dict[str, object]:
    """Serialize a journal row into the public event envelope.

    Note: v1 uses the journal UUID as `event_id` (the contract names ULID; a UUID is an
    equally unique, opaque transport dedup key). Revisit if ULIDs become required.
    """
    ts = row.created_at.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
    return {
        "event_id": str(row.id),
        "schema_version": SCHEMA_VERSION,
        "tenant_id": str(row.tenant_id),
        "session_id": str(row.session_id) if row.session_id is not None else None,
        "run_id": str(row.run_id),
        "session_seq": row.session_seq,
        "seq": row.run_seq,
        "ts": ts,
        "type": row.event_type,
        "payload": row.payload_redacted,
        "redaction": {"redacted": False, "truncated": False, "paths": []},
    }
