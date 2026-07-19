"""Effect invocation model (ADR-017). Schema/constraints in migration 0003."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, Integer, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EffectInvocation(Base):
    __tablename__ = "effect_invocations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    invocation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_seq: Mapped[int | None] = mapped_column(BigInteger)
    effect_name: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    effect_class: Mapped[str] = mapped_column(Text)
    retry_policy: Mapped[str] = mapped_column(Text)
    args_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text, server_default="prepared")
    outcome: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    reconciliation_state: Mapped[str] = mapped_column(Text, server_default="not_required")
    result_redacted: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    external_reference_redacted: Mapped[str | None] = mapped_column(Text)
    last_error_redacted: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
