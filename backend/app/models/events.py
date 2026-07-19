"""Event journal + outbox ORM models (ADR-016).

Schema/constraints live in migration 0002; these mirror it for reads.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EventJournal(Base):
    __tablename__ = "event_journal"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_seq: Mapped[int | None] = mapped_column(BigInteger)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_seq: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(Text)
    envelope_version: Mapped[int] = mapped_column(SmallInteger)
    durability: Mapped[str] = mapped_column(Text, server_default="durable")
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    causation_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload_redacted: Mapped[dict[str, object]] = mapped_column(JSONB)
    payload_size_bytes: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Outbox(Base):
    __tablename__ = "outbox"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    topic: Mapped[str] = mapped_column(Text)
    delivery_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
