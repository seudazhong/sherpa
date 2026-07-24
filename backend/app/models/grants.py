"""Pre-authorization grant model (ADR-034; migration 0024)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PermissionGrant(Base):
    __tablename__ = "permission_grants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    tool_name: Mapped[str] = mapped_column(Text)
    match_json: Mapped[dict] = mapped_column(JSONB)
    created_via: Mapped[str] = mapped_column(Text, server_default="manual")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
