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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    head_generation: Mapped[int] = mapped_column(Integer, server_default="0")
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
    activate ``projects.current_snapshot_id``. Every exit has a named reason.

    W2b (ADR-038) extends this with ``create_kind='github'``: the github columns carry
    the source spec + a ``connection_id`` reference (NOT the token — the token stays in
    ``github_connections``/vault); the worker resolves ref→OID then bounded-fetches the
    tarball for that OID."""

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
    # W2b GitHub columns (create_kind='github'); token stays in github_connections.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_ref_type: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    resolved_oid: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectSource(Base):
    """Canonical GitHub source provenance (ADR-038, W2b): one row per project once a
    github import starts. After a successful import this is a frozen provenance record
    (repo id + ref + resolved OID); the remote is NOT authoritative and W2b never
    re-fetches. ``connection_id`` is a vault credential reference, never the token."""

    __tablename__ = "project_sources"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(Text, server_default="github")
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    repo_external_id: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(Text)
    repo: Mapped[str] = mapped_column(Text)
    ref_type: Mapped[str] = mapped_column(Text)
    ref_name: Mapped[str] = mapped_column(Text)
    source_oid: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="importing")
    imported_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Workspace W3 (ADR-040 + ADR-039; migration 0030) ------------------------
# A Project-bound Chat's first mutating action opens a DURABLE task working copy from
# the current Project head. The scratch tree / warm container are rebuildable caches of
# these rows (never recovery truth). File bytes are the shared ADR-030 storage_blobs;
# bytes/credentials never enter the journal.


class ProjectWorkingCopy(Base):
    """One durable pending task working copy, owned by exactly one Project-bound Chat
    (session) + Project. Authoritative pending state that spans chat turns. Single-writer:
    (lease_owner, lease_expires_at) gives mutual exclusion; ``fence_token`` (monotonic,
    bumped on lease (re)acquire) is stamped on every overlay/change-set publish so a STALE
    sandbox can never publish a later overlay."""

    __tablename__ = "project_working_copies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    base_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    base_head_generation: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(Text, server_default="open")
    version: Mapped[int] = mapped_column(Integer, server_default="0")
    fence_token: Mapped[int] = mapped_column(BigInteger, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    overlay_entry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    overlay_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_boundary_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectWorkingCopyEntry(Base):
    """The durable overlay: the working copy's delta vs its base snapshot, sufficient
    (with the base) to rebuild the exact scratch tree. A 'deleted' entry is a whiteout
    over a base path; file bytes are immutable ADR-030 storage_blobs."""

    __tablename__ = "project_working_copy_entries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    working_copy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    path: Mapped[str] = mapped_column(Text)
    change_kind: Mapped[str] = mapped_column(Text)
    entry_kind: Mapped[str] = mapped_column(Text, server_default="file")
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    executable: Mapped[bool] = mapped_column(Boolean, server_default="false")
    symlink_target: Mapped[str | None] = mapped_column(Text)
    fence_token: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProjectChangeSet(Base):
    """A bounded, REVIEWABLE change set produced at an execution boundary: overlay-vs-base
    compared, bounds enforced, ready for the human Change Review UI. Durable projection."""

    __tablename__ = "project_change_sets"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    working_copy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    base_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    fence_token: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text, server_default="open")
    added_count: Mapped[int] = mapped_column(Integer, server_default="0")
    modified_count: Mapped[int] = mapped_column(Integer, server_default="0")
    deleted_count: Mapped[int] = mapped_column(Integer, server_default="0")
    artifact_count: Mapped[int] = mapped_column(Integer, server_default="0")
    changed_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    diff_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    truncated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectChangeSetEntry(Base):
    """One reviewable file change in a change set. Save-selected applies the subset with
    ``selected=true``; a bounded textual diff MAY be spilled to MinIO (diff_object_key)."""

    __tablename__ = "project_change_set_entries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    change_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    path: Mapped[str] = mapped_column(Text)
    change_kind: Mapped[str] = mapped_column(Text)
    old_content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    new_content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    executable: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_binary: Mapped[bool] = mapped_column(Boolean, server_default="false")
    diff_object_key: Mapped[str | None] = mapped_column(Text)
    diff_truncated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    selected: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProjectArtifact(Base):
    """Run OUTPUTS that are not project files (test/build logs, generated reports).
    Ephemeral by default: charges quota ONLY after explicit Keep/Export."""

    __tablename__ = "project_artifacts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    working_copy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="file")
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    mime: Mapped[str | None] = mapped_column(Text)
    retention: Mapped[str] = mapped_column(Text, server_default="ephemeral")
    retained_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProjectRuntimeSession(Base):
    """One coding RuntimeSession (open -> exec* -> close): links a chat session (and, for
    ``scope='project'``, a working copy) to a container. BOUNDED, NON-AUTHORITATIVE
    operational record — the container is a rebuildable cache, NEVER recovery truth.
    ``scope='ephemeral'`` carries no project/working copy and replaces the deleted
    ``run_code`` (ADR-048 §3). ``uq_prs_live`` allows at most one live session per working
    copy, mirroring the single-writer lease.

    Replaces ``project_sandbox_runs`` (ADR-047 + ADR-048): ``scratch_ref`` is meaningless
    under tar transport and ``warm_until`` was never implemented — the idle TTL is
    ``expires_at``. The service/tool layer that drives these rows lands in Phase TR P4;
    the table exists from the 0001 baseline so the schema never needs a second migration.
    """

    __tablename__ = "project_runtime_sessions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    working_copy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(Text, server_default="project")
    base_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fence_token: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text, server_default="opening")
    container_ref: Mapped[str | None] = mapped_column(Text)
    image: Mapped[str] = mapped_column(Text)
    image_digest: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    ingress_bytes: Mapped[int | None] = mapped_column(BigInteger)
    entry_count: Mapped[int | None] = mapped_column(Integer)
    termination_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectExecRun(Base):
    """One command executed inside a runtime session, with its OWN named termination
    reason (events §2.11 ④ — the fix for backlog B-8's blanket collapse). ``run_id`` is the
    durable model-loop run when the command was agent-driven and NULL when a human pressed
    Run. An exec is not durably complete until ``persisted_boundary_at`` is set (overlay +
    change set committed)."""

    __tablename__ = "project_exec_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    runtime_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(Integer)
    command_preview: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(Boolean, server_default="false")
    termination_reason: Mapped[str | None] = mapped_column(Text)
    output_truncated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    spill_ref: Mapped[str | None] = mapped_column(Text)
    change_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    persisted_boundary_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
