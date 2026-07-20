"""Audit receipt model (ADR-021). Schema/constraints in migration 0013.

The semantic activity ledger: one row per meaningful read / inference / action
Sherpa performs on the user's behalf. Append-only at the application layer.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditReceipt(Base):
    __tablename__ = "audit_receipts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    receipt_version: Mapped[int] = mapped_column(SmallInteger)
    receipt_type: Mapped[str] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trigger_type: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approval_envelope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    reversible: Mapped[bool] = mapped_column(Boolean, server_default="false")
    summary_redacted: Mapped[dict[str, object]] = mapped_column(JSONB)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
