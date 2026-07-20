"""Data controls (ADR-021): export and erase a user's imported connector data.

* ``export_imported_data`` returns a bounded, JSON-safe bundle of everything Sherpa
  imported and derived (connectors, items, candidates, todos) plus the activity
  ledger, so the user can take their data with them.
* ``delete_imported_data`` erases imported items and everything derived from them
  (candidates, todos, extractions, generations) in foreign-key-safe order. The
  audit ledger and run history are retained as the record of what happened.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditReceipt,
    Candidate,
    Connector,
    ConnectorItem,
    Extraction,
    Generation,
    Todo,
)

_CAP = 1000


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _row(model: Any, columns: list[str]) -> dict[str, Any]:
    return {c: _jsonable(getattr(model, c)) for c in columns}


async def export_imported_data(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Return a bounded, JSON-safe bundle of the tenant's imported + derived data."""

    async def _all(model: Any, order: Any, columns: list[str]) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    select(model).where(model.tenant_id == tenant_id).order_by(order).limit(_CAP)
                )
            )
            .scalars()
            .all()
        )
        return [_row(r, columns) for r in rows]

    return {
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "tenant_id": str(tenant_id),
        "connectors": await _all(
            Connector,
            Connector.created_at,
            ["id", "kind", "external_account_id", "status", "created_at", "last_sync_at"],
        ),
        "connector_items": await _all(
            ConnectorItem,
            ConnectorItem.received_at,
            ["id", "connector_id", "provider_item_id", "revision", "received_at", "content_json"],
        ),
        "candidates": await _all(
            Candidate,
            Candidate.created_at,
            ["id", "status", "title", "description", "due_at", "priority", "created_at"],
        ),
        "todos": await _all(
            Todo,
            Todo.created_at,
            ["id", "source_candidate_id", "title", "status", "due_at", "created_at"],
        ),
        "activity": await _all(
            AuditReceipt,
            AuditReceipt.occurred_at,
            ["id", "receipt_type", "action", "outcome", "summary_redacted", "occurred_at"],
        ),
    }


async def delete_imported_data(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    """Erase imported items + derived candidates/todos/extractions in FK-safe order.

    Runs, effect invocations, approvals, and audit receipts are retained as the
    immutable record of what Sherpa did. Caller commits.
    """
    counts: dict[str, int] = {}
    # Child -> parent. candidate<->todo FKs are DEFERRABLE (validated at COMMIT).
    for name, model in (
        ("todos", Todo),
        ("candidates", Candidate),
        ("generations", Generation),
        ("extractions", Extraction),
        ("connector_items", ConnectorItem),
    ):
        result = await session.execute(delete(model).where(model.tenant_id == tenant_id))
        counts[name] = cast("CursorResult[Any]", result).rowcount or 0
    return counts
