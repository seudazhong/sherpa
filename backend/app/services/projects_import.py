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

import dataclasses
import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    GithubConnection,
    Project,
    ProjectImportJob,
    ProjectSource,
)
from app.objectstore import build_object_store
from app.services import github_source as gh
from app.services import projects as projects_svc
from app.services.archive import ArchiveBounds, ArchiveEntry, ArchiveError, expand_archive
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


async def create_github_import(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    name: str,
    repo_external_id: str,
    owner: str,
    repo: str,
    ref_type: str,
    ref: str,
    connection_id: uuid.UUID | None,
) -> tuple[Project, ProjectImportJob]:
    """Create a no-snapshot project + a queued **github** import job + a ``project_sources``
    provenance row (status='importing'). Validates the spec synchronously: an active
    connection is required (409), the ref spec must be well-formed (422). The token is
    NOT copied here — the job carries only a ``connection_id`` reference. Caller commits
    then enqueues. The durable worker resolves ref→OID + fetches the tarball."""
    uid = projects_svc._require_user(ctx)
    name = projects_svc._validate_name(name)
    if ref_type not in settings.github_import_ref_types:
        raise Invalid(f"unsupported ref_type: {ref_type}")
    if ref_type not in ("branch", "tag", "commit"):
        raise Invalid(f"unsupported ref_type: {ref_type}")
    if not (repo_external_id or "").strip() or not owner.strip() or not repo.strip():
        raise Invalid("repo_external_id, owner and repo are required")
    if not (ref or "").strip():
        raise Invalid("ref is required")
    await projects_svc._assert_name_free(db, ctx, uid, name)

    # W2b first version requires an active connection (public-repo-without-conn = later).
    conn = await gh.get_live_connection(db, ctx, uid)
    if conn is None or conn.status != "active" or conn.token_enc is None:
        raise Conflict("no active github connection")
    if connection_id is not None and connection_id != conn.id:
        raise Conflict("connection is not the owner's active connection")

    project = Project(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        name=name,
        status="active",
        source_status="importing",
        current_snapshot_id=None,
        last_activity_at=_now(),
    )
    db.add(project)
    await db.flush()

    source = ProjectSource(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=uid,
        provider="github",
        connection_id=conn.id,
        repo_external_id=str(repo_external_id).strip(),
        owner=owner.strip(),
        repo=repo.strip(),
        ref_type=ref_type,
        ref_name=ref.strip(),
        status="importing",
    )
    db.add(source)

    job = ProjectImportJob(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=uid,
        create_kind="github",
        stage="queued",
        idempotency_key=f"import:{project.id}",
        connection_id=conn.id,
        source_ref_type=ref_type,
        source_ref=ref.strip(),
    )
    db.add(job)
    await db.flush()
    return project, job


async def retry_github_import(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID
) -> Project:
    """Re-enqueue the durable github import for a failed project (idempotent on
    ``(project_id, import)``; read-only re-fetch by the resolved OID → identical bytes).
    Only valid while the project has no active snapshot (else 409). Caller commits then
    enqueues."""
    uid = projects_svc._require_user(ctx)
    project = await db.get(Project, (ctx.tenant_id, project_id))
    if project is None or project.user_id != uid or project.status == "deleting":
        raise projects_svc.NotFound("project not found")
    if project.current_snapshot_id is not None:
        raise Conflict("project already has a snapshot")
    job = await db.scalar(
        select(ProjectImportJob)
        .where(
            ProjectImportJob.tenant_id == ctx.tenant_id,
            ProjectImportJob.project_id == project_id,
        )
        .order_by(ProjectImportJob.created_at.desc())
        .limit(1)
    )
    if job is None or job.create_kind != "github":
        raise Conflict("no github import to retry")
    job.stage = "queued"
    job.lease_owner = None
    job.lease_expires_at = None
    job.termination_reason = None
    project.source_status = "importing"
    source = await _get_source(db, ctx.tenant_id, project_id)
    if source is not None:
        source.status = "importing"
    await db.flush()
    return project


async def _get_source(
    db: AsyncSession, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectSource | None:
    return await db.scalar(
        select(ProjectSource).where(
            ProjectSource.tenant_id == tenant_id,
            ProjectSource.project_id == project_id,
        )
    )


def _strip_top_level(entries: list[ArchiveEntry]) -> list[ArchiveEntry]:
    """GitHub tarballs nest everything under a single ``owner-repo-<sha>/`` root. Strip
    that common leading segment so the project tree is clean (README.md at the root, not
    ``repo-abc123/README.md``). If there is no single common root, entries are unchanged."""
    if not entries:
        return entries
    roots = {e.path.split("/", 1)[0] for e in entries}
    if len(roots) != 1:
        return entries
    root = next(iter(roots))
    stripped: list[ArchiveEntry] = []
    for e in entries:
        if e.path == root:
            continue  # the root dir entry itself disappears
        rest = e.path[len(root) + 1 :]
        if not rest:
            continue
        stripped.append(dataclasses.replace(e, path=rest))
    return stripped


async def _fail(job: ProjectImportJob, code: str) -> str:
    job.stage = "failed"
    job.termination_reason = code
    logger.warning(
        "project import failed",
        extra={"project_id": str(job.project_id), "reason": code},
    )
    return code


async def _fail_github(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project: Project,
    job: ProjectImportJob,
    code: str,
) -> str:
    """Fail a github import: mark job + project + provenance import_failed with no
    snapshot (visible + deletable). Never a snapshot over the wrong bytes."""
    project.source_status = "import_failed"
    source = await _get_source(db, tenant_id, project.id)
    if source is not None:
        source.status = "import_failed"
    return await _fail(job, code)


async def process_import(
    db: AsyncSession, *, tenant_id: uuid.UUID, project_id: uuid.UUID, lease_owner: str
) -> tuple[str, str | None]:
    """Run one import job to a named terminal reason. Caller commits. Returns
    (reason, staging_object_key) so the caller can delete the staging object post-commit
    (github imports have no staging object)."""
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

    if job.create_kind == "github":
        return await _process_github(db, tenant_id=tenant_id, project=project, job=job)

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


async def _process_github(
    db: AsyncSession, *, tenant_id: uuid.UUID, project: Project, job: ProjectImportJob
) -> tuple[str, str | None]:
    """GitHub one-time import: resolve ref → commit OID → bounded tarball fetch → reuse
    the W2a in-memory safe expander → immutable initial snapshot (source_oid set) →
    atomically activate + set source_status='imported' + freeze provenance. Read-only ⇒
    a failed/partial fetch is safely retryable (no effect_unknown). The GitHub token is
    decrypted only here and never logged / never enters the snapshot."""
    source = await _get_source(db, tenant_id, project.id)
    if source is None:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code="error:missing_source"
        ), None

    conn: GithubConnection | None = None
    if job.connection_id is not None:
        conn = await db.get(GithubConnection, (tenant_id, job.connection_id))
    if conn is None:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code="auth_required"
        ), None
    token = gh.open_connection_token_for_worker(conn)
    if token is None:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code="auth_required"
        ), None

    owner, repo = source.owner, source.repo
    ref_type = job.source_ref_type or source.ref_type
    ref = job.source_ref or source.ref_name

    try:
        async with gh.make_client() as client:
            oid = job.resolved_oid
            if not oid:
                oid = await gh.resolve_ref_to_oid(
                    client, token, owner=owner, repo=repo, ref_type=ref_type, ref=ref
                )
                job.resolved_oid = oid
                source.source_oid = oid
                await db.flush()
            raw = await gh.fetch_repo_tarball(
                client,
                token,
                owner=owner,
                repo=repo,
                oid=oid,
                max_bytes=settings.project_max_archive_bytes,
            )
    except gh.GithubApiError as exc:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code=exc.code
        ), None

    try:
        entries = _strip_top_level(expand_archive(raw, _bounds()))
    except ArchiveError as exc:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code=exc.code
        ), None

    ctx = CallerContext(tenant_id=tenant_id, user_id=project.user_id, actor="system")
    try:
        snapshot = await projects_svc.build_import_snapshot(
            db, ctx, project, entries, source_oid=job.resolved_oid
        )
    except InsufficientStorage:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code="too_large"
        ), None
    except (Invalid, Conflict) as exc:
        return await _fail_github(
            db, tenant_id=tenant_id, project=project, job=job, code=f"error:{exc.code}"
        ), None

    project.source_status = "imported"
    source.status = "imported"
    source.source_oid = job.resolved_oid
    source.imported_at = _now()
    job.stage = "done"
    job.termination_reason = "done"
    job.entry_count = snapshot.entry_count
    job.size_bytes = snapshot.size_bytes
    logger.info(
        "github import done",
        extra={
            "project_id": str(project.id),
            "repo": f"{owner}/{repo}",
            "ref_type": ref_type,
            "source_oid": job.resolved_oid,
            "entries": snapshot.entry_count,
            "bytes": snapshot.size_bytes,
        },
    )
    return "done", None


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
