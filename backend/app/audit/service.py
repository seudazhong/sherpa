"""Semantic activity ledger (ADR-021): record what Sherpa did on the user's behalf.

Three categories map to the frozen ``receipt_type`` vocabulary used by the UI:
``read`` (a connector pull), ``inference`` (a model extraction), and ``action``
(an external effect, e.g. a gated ``send_email``). Receipts are append-only.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditReceipt

READ = "read"
INFERENCE = "inference"
ACTION = "action"


async def record_receipt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    receipt_type: str,
    actor_type: str,
    trigger_type: str,
    action: str,
    outcome: str,
    summary: dict[str, object],
    actor_user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    invocation_id: uuid.UUID | None = None,
    approval_envelope_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    source_event_id: uuid.UUID | None = None,
    reversible: bool = False,
    occurred_at: datetime.datetime | None = None,
) -> AuditReceipt:
    """Persist one audit receipt. Caller commits. Never raises on the happy path."""
    receipt = AuditReceipt(
        tenant_id=tenant_id,
        id=uuid.uuid4(),
        receipt_version=1,
        receipt_type=receipt_type,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        trigger_type=trigger_type,
        run_id=run_id,
        invocation_id=invocation_id,
        approval_envelope_id=approval_envelope_id,
        subject_type=subject_type,
        subject_id=subject_id,
        action=action,
        outcome=outcome,
        reversible=reversible,
        summary_redacted=summary,
        source_event_id=source_event_id,
        occurred_at=occurred_at or datetime.datetime.now(datetime.UTC),
    )
    session.add(receipt)
    await session.flush()
    return receipt
