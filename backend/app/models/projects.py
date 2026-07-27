"""Workspace Projects models (ADR-037, W2a; migration 0028).

A Project is a named, durable, user-visible development state whose immutable head
``project_snapshots`` reference the same ADR-030 content-addressed ``storage_blobs``
as Drive (shared dedup + quota). W2a only ever creates ``reason='import'`` snapshots
(the initial snapshot of a blank/template/archive project). Archive import is a
durable job (``project_import_jobs``: lease + idempotency + named termination reason)
that realizes the events §2.9 ``project.lifecycle`` stages. Every table carries
``tenant_id`` + composite tenant-scoped keys (ADR-015).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Project(Base):
    __tablename__ = "projects"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    current_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    default_branch_label: Mapped[str] = mapped_column(Text, server_default="main")
    source_status: Mapped[str] = mapped_column(Text, server_default="unbound")
    used_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_activity_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(Text)
    entry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    source_oid: Mapped[str | None] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProjectSnapshotEntry(Base):
    __tablename__ = "project_snapshot_entries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    path: Mapped[str] = mapped_column(Text)
    entry_kind: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    executable: Mapped[bool] = mapped_column(Boolean, server_default="false")
    symlink_target: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProjectImportJob(Base):
    """Durable archive-import job (events §2.9 realization; mirrors the knowledge
    ingestion job). Recovery source of truth: claim (lease) → stage the archive in
    isolated bounded expansion → build the initial immutable snapshot → atomically
    activate ``projects.current_snapshot_id``. Every exit has a named reason."""

    __tablename__ = "project_import_jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    create_kind: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, server_default="queued")
    idempotency_key: Mapped[str] = mapped_column(Text)
    staging_object_key: Mapped[str | None] = mapped_column(Text)
    archive_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    entry_count: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    termination_reason: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
