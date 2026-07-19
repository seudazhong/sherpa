"""Operational telemetry models (docs/07). Mutable/retained, not an audit API.

`traces` is the per-run span projected from the event journal. `generations` and
`audit_receipts` land in M2 (they depend on M2 tables extractions /
approval_envelopes). Mirrors docs/contracts/data-model.md; CHECKs live in the
migration.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["sessions.tenant_id", "sessions.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="RESTRICT"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    parent_trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trace_kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
