"""Session search projection model (ADR-029 P1; migration 0020).

Derived/rebuildable. The generated ``fts``/``cjk_fts`` tsvector columns are
DB-managed and intentionally not mapped here (never read in Python).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SessionSearchEntry(Base):
    __tablename__ = "session_search_entries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_kind: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    anchor_kind: Mapped[str] = mapped_column(Text)
    anchor_id: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    message_seq: Mapped[int | None] = mapped_column(BigInteger)
    event_session_seq: Mapped[int | None] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(Text)
    content_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    cjk_terms: Mapped[str] = mapped_column(Text, server_default="")
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    projection_version: Mapped[int] = mapped_column(Integer, server_default="1")
    redacted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
