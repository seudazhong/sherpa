"""Scheduler models: schedules + schedule_firings (docs/06, ADR-017).

Mirrors contracts/data-model.md; CHECKs live in the migration. A firing's unique
(schedule, scheduled_for) slot makes a slot fire at most once (no double-fire).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "todo_id"], ["todos.tenant_id", "todos.id"], ondelete="RESTRICT"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    todo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(200))
    reminder_kind: Mapped[str | None] = mapped_column(Text)
    delivery_channel: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    local_time: Mapped[datetime.time | None] = mapped_column(Time)
    cadence_kind: Mapped[str] = mapped_column(Text, server_default="daily")
    cron_expr: Mapped[str | None] = mapped_column(Text)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    weekly_days: Mapped[str | None] = mapped_column(Text)
    monthly_day: Mapped[int | None] = mapped_column(SmallInteger)
    prompt: Mapped[str | None] = mapped_column(Text)
    next_fire_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    last_fired_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    misfire_policy: Mapped[str] = mapped_column(Text)
    duplicate_policy: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScheduleFiring(Base):
    __tablename__ = "schedule_firings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "schedule_id"],
            ["schedules.tenant_id", "schedules.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invocation_id"],
            ["effect_invocations.tenant_id", "effect_invocations.invocation_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="SET NULL"
        ),
        UniqueConstraint("tenant_id", "schedule_id", "firing_key", name="uq_schedule_firings_key"),
        UniqueConstraint(
            "tenant_id", "schedule_id", "scheduled_for", name="uq_schedule_firings_slot"
        ),
        UniqueConstraint(
            "tenant_id", "delivery_idempotency_key", name="uq_schedule_firings_delivery_key"
        ),
        UniqueConstraint("tenant_id", "invocation_id", name="uq_schedule_firings_invocation"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    schedule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    firing_key: Mapped[str] = mapped_column(Text)
    scheduled_for: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    delivery_outcome: Mapped[str | None] = mapped_column(Text)
    delivery_idempotency_key: Mapped[str] = mapped_column(Text)
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
