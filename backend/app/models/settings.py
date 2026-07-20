"""User notification/preference settings (contracts/data-model.md user_settings)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    web_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    email_digest_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    timezone: Mapped[str] = mapped_column(Text, server_default="UTC")
    digest_time: Mapped[datetime.time] = mapped_column(Time)
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    quiet_hours_start: Mapped[datetime.time] = mapped_column(Time)
    quiet_hours_end: Mapped[datetime.time] = mapped_column(Time)
    daily_cap: Mapped[int] = mapped_column(Integer, server_default="6")
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    eventual_delivery_kinds: Mapped[list[str]] = mapped_column(ARRAY(Text))
    connector_analysis: Mapped[str] = mapped_column(Text, server_default="candidate_first")
    todo_promotion: Mapped[str] = mapped_column(Text, server_default="manual")
    external_actions: Mapped[str] = mapped_column(Text, server_default="approval_required")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
