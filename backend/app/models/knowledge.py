"""Knowledge base models (ADR-036, migration 0027).

Source-backed document knowledge: canonical sources + versions (with immutable
object-store snapshots) and derived chunks (lexical `fts` + pgvector `embedding`),
plus durable ingestion jobs and retention-scoped retrieval evidence. Separate from
archival `memory_passages`. All tables carry tenant_id + composite keys (ADR-015).
"""

from __future__ import annotations

import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Double,
    Integer,
    LargeBinary,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmbeddingProfile(Base):
    __tablename__ = "embedding_profiles"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    dim: Mapped[int] = mapped_column(Integer)
    normalize: Mapped[str] = mapped_column(Text, server_default="cosine")
    privacy: Mapped[str] = mapped_column(Text, server_default="local")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_kind: Mapped[str] = mapped_column(Text, server_default="file")
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    display_name: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, server_default="private")
    trust_level: Mapped[str] = mapped_column(Text, server_default="untrusted")
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    desired_generation: Mapped[int] = mapped_column(Integer, server_default="1")
    tombstoned_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeSourceVersion(Base):
    __tablename__ = "knowledge_source_versions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    expected_file_version: Mapped[int | None] = mapped_column(Integer)
    expected_file_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    snapshot_object_key: Mapped[str] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(Text)
    pipeline_version: Mapped[str] = mapped_column(Text)
    embedding_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    language: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="building")
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    failure_code: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    activated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    text_content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    char_offset: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    lexical_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    fts: Mapped[str | None] = mapped_column(TSVECTOR)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeIngestionJob(Base):
    __tablename__ = "knowledge_ingestion_jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(Text, server_default="queued")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    termination_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeRetrievalEvidence(Base):
    __tablename__ = "knowledge_retrieval_evidence"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    retrieval_invocation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tool_call_id: Mapped[str | None] = mapped_column(Text)
    citation_ref: Mapped[str] = mapped_column(Text)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    excerpt: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Double)
    matched_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    purge_after: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
