"""Connector-analysis pipeline models: extraction -> generation -> candidate.

Mirrors contracts/data-model.md; CHECKs + the deferred candidate->todo link live
in the migrations. The provenance chain (connector_item -> extraction ->
generation -> candidate) is enforced by composite FKs.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "connector_item_id"],
            ["connector_items.tenant_id", "connector_items.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "tenant_id",
            "connector_item_id",
            "extraction_version",
            name="uq_extractions_item_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "connector_item_id",
            "extraction_version",
            name="uq_extractions_chain",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    connector_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    extraction_version: Mapped[int] = mapped_column(Integer)
    extractor_version: Mapped[str] = mapped_column(Text)
    output_schema_version: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error_redacted: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Generation(Base):
    __tablename__ = "generations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "trace_id"], ["traces.tenant_id", "traces.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_id"],
            ["extractions.tenant_id", "extractions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", "extraction_id", name="uq_generations_chain"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    response_schema_version: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, server_default="1")
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_id"],
            ["extractions.tenant_id", "extractions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "generation_id", "extraction_id"],
            ["generations.tenant_id", "generations.id", "generations.extraction_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "decided_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_candidates_dedupe"),
        UniqueConstraint(
            "tenant_id", "extraction_id", "ordinal", name="uq_candidates_extraction_ordinal"
        ),
        UniqueConstraint(
            "tenant_id", "id", "generation_id", "extraction_id", name="uq_candidates_chain"
        ),
        UniqueConstraint("tenant_id", "id", "accepted_todo_id", name="uq_candidates_todo_link"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    extraction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(Text, server_default="medium")
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    rationale_redacted: Mapped[str | None] = mapped_column(Text)
    source_excerpt_redacted: Mapped[str | None] = mapped_column(Text)
    accepted_todo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("tenant_id", "source_candidate_id", name="uq_todos_source_candidate"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(Text, server_default="gmail_candidate")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(Text, server_default="medium")
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
