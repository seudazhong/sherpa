"""Durable Project archive-import job (ADR-037, W2a; events §2.9 realization).

Archive uploads are untrusted. This durable stage machine is the recovery source of
truth (mirrors the knowledge ingestion job): **claim** (lease) → **stage** (read the
isolated staging object) → **expand** (bounded, in-memory, path-safe — never extracted
to disk) → **materialize** the initial immutable snapshot + atomically set
``projects.current_snapshot_id``. Every exit has a named ``termination_reason``. On any
failure the project stays with **no snapshot** (visible + deletable), never a snapshot
over the wrong bytes. Re-running a completed job is a no-op (idempotent per project).
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.files import build_object_store
from app.models import Project, ProjectImportJob
from app.services import projects as projects_svc
from app.services.archive import ArchiveBounds, ArchiveError, expand_archive
from app.services.context import CallerContext
from app.services.errors import Conflict, InsufficientStorage, Invalid, TooLarge

logger = logging.getLogger("app.projects.import")

_LEASE_SECONDS = 600


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _bounds() -> ArchiveBounds:
    return ArchiveBounds(
        max_expanded_bytes=settings.project_max_expanded_bytes,
        max_entries=settings.project_max_entries,
        max_expansion_ratio=settings.project_max_expansion_ratio,
        max_path_depth=settings.project_max_path_depth,
    )


async def create_archive_import(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    name: str,
    archive_bytes: bytes,
) -> tuple[Project, ProjectImportJob]:
    """Create a no-snapshot project + a queued import job, staging the archive bytes
    to an isolated object key. Returns (project, job); the caller commits then enqueues.
    Raises TooLarge over the compressed-upload cap, Conflict on a duplicate name."""
    uid = projects_svc._require_user(ctx)
    name = projects_svc._validate_name(name)
    if not archive_bytes:
        raise Invalid("empty archive upload")
    if len(archive_bytes) > settings.project_max_archive_bytes:
        raise TooLarge(f"archive exceeds {settings.project_max_archive_bytes} bytes")
    await projects_svc._assert_name_free(db, ctx, uid, name)

    project = Project(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        name=name,
        status="active",
        source_status="unbound",
        current_snapshot_id=None,
        last_activity_at=_now(),
    )
    db.add(project)
    await db.flush()

    job_id = uuid.uuid4()
    staging_key = f"project-import/{project.id}/{job_id}"
    await build_object_store().put(staging_key, archive_bytes, "application/zip")
    job = ProjectImportJob(
        tenant_id=ctx.tenant_id,
        id=job_id,
        project_id=project.id,
        user_id=uid,
        create_kind="archive",
        stage="queued",
        idempotency_key=f"import:{project.id}",
        staging_object_key=staging_key,
        archive_bytes=len(archive_bytes),
    )
    db.add(job)
    await db.flush()
    return project, job


async def _fail(job: ProjectImportJob, code: str) -> str:
    job.stage = "failed"
    job.termination_reason = code
    logger.warning(
        "project import failed",
        extra={"project_id": str(job.project_id), "reason": code},
    )
    return code


async def process_import(
    db: AsyncSession, *, tenant_id: uuid.UUID, project_id: uuid.UUID, lease_owner: str
) -> tuple[str, str | None]:
    """Run one import job to a named terminal reason. Caller commits. Returns
    (reason, staging_object_key) so the caller can delete the staging object post-commit."""
    project = await db.get(Project, (tenant_id, project_id))
    job = await db.scalar(
        select(ProjectImportJob)
        .where(
            ProjectImportJob.tenant_id == tenant_id,
            ProjectImportJob.project_id == project_id,
        )
        .order_by(ProjectImportJob.created_at.desc())
        .limit(1)
    )
    if project is None or job is None:
        return "missing", None
    if job.stage in ("done", "failed"):
        return f"already_{job.stage}", job.staging_object_key
    if project.current_snapshot_id is not None:
        job.stage = "done"
        job.termination_reason = "done"
        return "done", job.staging_object_key

    # Claim (lease).
    job.lease_owner = lease_owner
    job.lease_expires_at = _now() + datetime.timedelta(seconds=_LEASE_SECONDS)
    job.attempt += 1
    job.stage = "staged"

    if not job.staging_object_key:
        return await _fail(job, "staging_missing"), None
    staging_key = job.staging_object_key
    try:
        raw = await build_object_store().get(staging_key)
    except Exception:  # noqa: BLE001 - a missing staging object is a named exit
        return await _fail(job, "staging_missing"), staging_key

    try:
        entries = expand_archive(raw, _bounds())
    except ArchiveError as exc:
        return await _fail(job, exc.code), staging_key

    ctx = CallerContext(tenant_id=tenant_id, user_id=project.user_id, actor="system")
    try:
        snapshot = await projects_svc.build_import_snapshot(db, ctx, project, entries)
    except InsufficientStorage:
        return await _fail(job, "quota_exceeded"), staging_key
    except (Invalid, Conflict) as exc:
        return await _fail(job, f"error:{exc.code}"), staging_key

    job.stage = "done"
    job.termination_reason = "done"
    job.entry_count = snapshot.entry_count
    job.size_bytes = snapshot.size_bytes
    logger.info(
        "project import done",
        extra={
            "project_id": str(project_id),
            "entries": snapshot.entry_count,
            "bytes": snapshot.size_bytes,
        },
    )
    return "done", staging_key


async def recover_stuck_imports(
    db: AsyncSession, *, limit: int = 50
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant_id, project_id) for import jobs to (re)dispatch: queued, or claimed with
    an expired lease. At-least-once recovery for the archive-import pipeline."""
    now = _now()
    rows = (
        (
            await db.execute(
                select(ProjectImportJob.tenant_id, ProjectImportJob.project_id)
                .where(
                    ProjectImportJob.stage.not_in(("done", "failed")),
                    (ProjectImportJob.lease_expires_at.is_(None))
                    | (ProjectImportJob.lease_expires_at < now),
                )
                .limit(limit)
            )
        )
        .tuples()
        .all()
    )
    return [(t, p) for t, p in rows]
