"""User-private core memory model (ADR-004; migration 0015).

Bounded key-value facts the agent keeps about the user across sessions. No
embeddings — the vector/RAG passage tier is deferred (ADR-012/022).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserMemory(Base):
    __tablename__ = "user_memory"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    memory_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
