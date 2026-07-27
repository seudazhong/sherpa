"""Projects REST surface (api.md §10.5; ADR-037 W2a; ADR-023 parity with project_* tools).

Thin adapter over ``app.services.projects`` (+ the durable archive-import job) so the
Projects UI (`/work/projects`) and the agent tools share one capability layer. Reads
need a session; writes also need CSRF. W2a covers blank / template / archive projects,
Project detail, and **Open in Chat** (an immutable Project-bound session). **GitHub
import returns 501** — it lands in W2b. Ingestion of an archive is enqueued best-effort
after commit; the worker's recovery tick guarantees at-least-once dispatch.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue
from app.api.drive import DriveNode, _node
from app.api.schemas import SessionSummary
from app.api.sessions import _summary as _session_summary
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import (
    Project,
    ProjectArtifact,
    ProjectChangeSet,
    ProjectSandboxRun,
    ProjectSource,
    ProjectWorkingCopy,
)
from app.services import CallerContext, ServiceError
from app.services import github_source as gh
from app.services import project_changes as changes_svc
from app.services import project_sandbox as sbx_svc
from app.services import project_workcopy as wc_svc
from app.services import projects as svc
from app.services import projects_import as pimp
from app.services import sessions as sessions_svc

logger = logging.getLogger("app.api.projects")
router = APIRouter(tags=["projects"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class ProjectSourceOut(BaseModel):
    provider: Literal["github"]
    repo_external_id: str
    owner: str
    repo: str
    ref_type: Literal["branch", "tag", "commit"]
    ref_name: str
    source_oid: str | None
    status: Literal["importing", "imported", "import_failed"]
    imported_at: datetime.datetime | None


class ProjectSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: Literal["active", "archived", "deleting"]
    source_status: Literal["unbound", "importing", "imported", "import_failed"]
    current_snapshot_id: uuid.UUID | None
    used_bytes: int
    last_activity_at: datetime.datetime | None
    updated_at: datetime.datetime
    import_status: Literal["none", "importing", "ready", "failed"]
    import_failure_reason: str | None
    source: ProjectSourceOut | None = None


class ProjectPage(BaseModel):
    items: list[ProjectSummary]
    next_cursor: str | None


class ProjectCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None
    template_id: str | None = None


class ProjectEntry(BaseModel):
    path: str
    entry_kind: Literal["file", "dir", "symlink"]
    size_bytes: int
    executable: bool


class ProjectTree(BaseModel):
    project_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    entries: list[ProjectEntry]
    returned_count: int
    truncated: bool


class ProjectSnapshotOut(BaseModel):
    id: uuid.UUID
    reason: str
    entry_count: int
    size_bytes: int
    pinned: bool
    created_at: datetime.datetime


class ProjectChatCreate(BaseModel):
    title: str | None = None


class SandboxRunState(BaseModel):
    run_id: uuid.UUID
    state: Literal["materializing", "running", "persisted", "failed", "timed_out"]
    warm: bool
    exit_code: int | None
    timed_out: bool
    termination_reason: str | None


class WorkingCopySummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    session_id: uuid.UUID
    base_snapshot_id: uuid.UUID
    state: Literal["open", "ready_for_review", "saved", "discarded", "conflicted", "expired"]
    overlay_entry_count: int
    overlay_bytes: int
    reserved_bytes: int
    head_moved: bool
    open_change_set_id: uuid.UUID | None
    sandbox: SandboxRunState | None
    last_boundary_at: datetime.datetime | None
    expires_at: datetime.datetime | None
    updated_at: datetime.datetime


class ProjectContextOut(BaseModel):
    session_id: uuid.UUID
    project_id: uuid.UUID | None
    project_name: str | None
    bound: bool
    working_copy: WorkingCopySummary | None = None


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str


class GithubImportSpecIn(BaseModel):
    repo_external_id: str
    owner: str
    repo: str
    ref_type: Literal["branch", "tag", "commit"]
    ref: str
    connection_id: uuid.UUID | None = None


class GithubImportRequest(BaseModel):
    kind: Literal["github"]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    github: GithubImportSpecIn


class GithubRepoOut(BaseModel):
    repo_external_id: str
    owner: str
    repo: str
    private: bool
    default_branch: str


class GithubRepoPage(BaseModel):
    items: list[GithubRepoOut]
    next_cursor: str | None


class GithubRefOut(BaseModel):
    ref_type: Literal["branch", "tag"]
    name: str
    oid: str


def _source_out(src: ProjectSource | None) -> ProjectSourceOut | None:
    if src is None:
        return None
    return ProjectSourceOut(
        provider="github",
        repo_external_id=src.repo_external_id,
        owner=src.owner,
        repo=src.repo,
        ref_type=src.ref_type,  # type: ignore[arg-type]
        ref_name=src.ref_name,
        source_oid=src.source_oid,
        status=src.status,  # type: ignore[arg-type]
        imported_at=src.imported_at,
    )


def _summary(item: svc.ProjectListItem, source: ProjectSource | None = None) -> ProjectSummary:
    p = item.project
    return ProjectSummary(
        id=p.id,
        name=p.name,
        description=p.description,
        status=p.status,  # type: ignore[arg-type]
        source_status=p.source_status,  # type: ignore[arg-type]
        current_snapshot_id=p.current_snapshot_id,
        used_bytes=p.used_bytes,
        last_activity_at=p.last_activity_at,
        updated_at=p.updated_at,
        import_status=item.import_status,  # type: ignore[arg-type]
        import_failure_reason=item.import_failure_reason,
        source=_source_out(source),
    )


@router.get("/projects/templates")
async def list_templates(
    ctx: Annotated[RequestContext, Depends(require_context)],
) -> list[TemplateOut]:
    return [TemplateOut(**t) for t in svc.list_templates()]


@router.get("/projects")
async def list_projects(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    query: str | None = None,
    project_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ProjectPage:
    try:
        items = await svc.list_projects(
            db, _caller(ctx), query=query, status=project_status, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None
    return ProjectPage(items=[_summary(i) for i in items], next_cursor=None)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectSummary:
    try:
        await svc.create_project(
            db,
            _caller(ctx),
            name=body.name,
            description=body.description,
            template_id=body.template_id,
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    # Re-read as a list item (import_status derived) for the response.
    items = await svc.list_projects(db, _caller(ctx), limit=100)
    match = next((i for i in items if i.project.name == body.name), None)
    if match is None:
        raise HTTPException(status_code=500, detail="created project not found")
    return _summary(match)


@router.post("/projects/imports", status_code=status.HTTP_202_ACCEPTED)
async def import_project(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectSummary:
    """Durable async import. **archive** = multipart upload (bounded safe expand); a
    **github** one-time import = a JSON body carrying a GithubImportSpec (ADR-038).
    Both return `202` with `import_status='importing'` (no snapshot yet)."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return await _import_archive(request, ctx, db)
    return await _import_github(request, ctx, db)


async def _import_archive(
    request: Request, ctx: RequestContext, db: AsyncSession
) -> ProjectSummary:
    form = await request.form()
    kind = str(form.get("kind") or "")
    name = str(form.get("name") or "")
    if kind == "github":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="github import uses a JSON body",
        )
    if kind != "archive":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bad_kind")
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="archive file required"
        )
    data = await upload.read()
    try:
        project, _job = await pimp.create_archive_import(
            db, _caller(ctx), name=name, archive_bytes=data
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    try:
        await queue.enqueue_project_import(ctx.tenant_id, project.id)
    except Exception as exc:  # noqa: BLE001 - the recovery tick is the safety net
        logger.warning("project import enqueue skipped: %s", exc)
    item = await svc.get_list_item(db, _caller(ctx), project_id=project.id)
    return _summary(item)


async def _import_github(request: Request, ctx: RequestContext, db: AsyncSession) -> ProjectSummary:
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid json"
        ) from None
    try:
        body = GithubImportRequest.model_validate(raw)
    except Exception:  # noqa: BLE001 - pydantic validation → 422
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bad_github_spec"
        ) from None
    spec = body.github
    try:
        project, _job = await pimp.create_github_import(
            db,
            _caller(ctx),
            name=body.name,
            repo_external_id=spec.repo_external_id,
            owner=spec.owner,
            repo=spec.repo,
            ref_type=spec.ref_type,
            ref=spec.ref,
            connection_id=spec.connection_id,
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    try:
        await queue.enqueue_project_import(ctx.tenant_id, project.id)
    except Exception as exc:  # noqa: BLE001 - the recovery tick is the safety net
        logger.warning("github import enqueue skipped: %s", exc)
    item = await svc.get_list_item(db, _caller(ctx), project_id=project.id)
    return _summary(item)


@router.post("/projects/{project_id}/imports/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_import(
    project_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectSummary:
    """Re-enqueue a failed github import (idempotent; read-only re-fetch by resolved OID
    → identical bytes). `409` once the project has an active snapshot."""
    try:
        await pimp.retry_github_import(db, _caller(ctx), project_id=project_id)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    try:
        await queue.enqueue_project_import(ctx.tenant_id, project_id)
    except Exception as exc:  # noqa: BLE001 - the recovery tick is the safety net
        logger.warning("github import retry enqueue skipped: %s", exc)
    item = await svc.get_list_item(db, _caller(ctx), project_id=project_id)
    src = await svc.get_source(db, _caller(ctx), project_id=project_id)
    return _summary(item, src)


@router.get("/projects/github/repos")
async def github_repos(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    query: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> GithubRepoPage:
    """Read-only repo picker, proxied server-side through the stored connection
    credential (the token never reaches the client). `409` when not connected, `502`
    on a redacted upstream GitHub error."""
    try:
        repos, next_cursor = await gh.list_repos(
            db, _caller(ctx), query=query, cursor=cursor, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None
    return GithubRepoPage(
        items=[
            GithubRepoOut(
                repo_external_id=r.repo_external_id,
                owner=r.owner,
                repo=r.repo,
                private=r.private,
                default_branch=r.default_branch,
            )
            for r in repos
        ],
        next_cursor=next_cursor,
    )


@router.get("/projects/github/refs")
async def github_refs(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    repo_external_id: str,
    kind: str | None = None,
    query: str | None = None,
) -> list[GithubRefOut]:
    """Read-only ref picker (branches/tags) for a repo by stable numeric id."""
    try:
        refs = await gh.list_refs(
            db, _caller(ctx), repo_external_id=repo_external_id, kind=kind, query=query
        )
    except ServiceError as e:
        raise _http(e) from None
    return [GithubRefOut(ref_type=r.ref_type, name=r.name, oid=r.oid) for r in refs]  # type: ignore[arg-type]


@router.get("/projects/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectSummary:
    try:
        item = await svc.get_list_item(db, _caller(ctx), project_id=project_id)
        src = await svc.get_source(db, _caller(ctx), project_id=project_id)
    except ServiceError as e:
        raise _http(e) from None
    return _summary(item, src)


@router.get("/projects/{project_id}/tree")
async def get_tree(
    project_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    snapshot: uuid.UUID | None = None,
    path: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ProjectTree:
    try:
        tree = await svc.get_tree(
            db, _caller(ctx), project_id=project_id, snapshot_id=snapshot, path=path, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None
    return ProjectTree(
        project_id=tree.project_id,
        snapshot_id=tree.snapshot_id,
        entries=[
            ProjectEntry(
                path=e.path,
                entry_kind=e.entry_kind,  # type: ignore[arg-type]
                size_bytes=e.size_bytes,
                executable=e.executable,
            )
            for e in tree.entries
        ],
        returned_count=len(tree.entries),
        truncated=tree.truncated,
    )


@router.get("/projects/{project_id}/snapshots")
async def list_snapshots(
    project_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProjectSnapshotOut]:
    try:
        rows = await svc.list_snapshots(db, _caller(ctx), project_id=project_id)
    except ServiceError as e:
        raise _http(e) from None
    return [
        ProjectSnapshotOut(
            id=s.id,
            reason=s.reason,
            entry_count=s.entry_count,
            size_bytes=s.size_bytes,
            pinned=s.pinned,
            created_at=s.created_at,
        )
        for s in rows
    ]


@router.post("/projects/{project_id}/chats", status_code=status.HTTP_201_CREATED)
async def open_in_chat(
    project_id: uuid.UUID,
    body: ProjectChatCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SessionSummary:
    try:
        session = await svc.open_in_chat(db, _caller(ctx), project_id=project_id, title=body.title)
        view = await sessions_svc.get_view(db, _caller(ctx), session.id)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return _session_summary(view)


@router.get("/sessions/{session_id}/project-context")
async def get_project_context(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectContextOut:
    try:
        pc = await svc.project_context(db, _caller(ctx), session_id=session_id)
        wc = await wc_svc.get_live(db, _caller(ctx), session_id=session_id)
        wc_out = await _wc_summary(db, _caller(ctx), wc) if wc is not None else None
    except ServiceError as e:
        raise _http(e) from None
    return ProjectContextOut(
        session_id=pc.session_id,
        project_id=pc.project_id,
        project_name=pc.project_name,
        bound=pc.bound,
        working_copy=wc_out,
    )


# --- Workspace W3: working copy / sandbox / change review (api.md §10.7) -----


class SandboxRunRequest(BaseModel):
    command: Annotated[str, Field(min_length=1, max_length=4000)]


class ChangeSetEntrySummary(BaseModel):
    id: uuid.UUID
    path: str
    change_kind: Literal["added", "modified", "deleted"]
    size_bytes: int
    executable: bool
    is_binary: bool
    has_diff: bool
    diff_truncated: bool
    selected: bool


class ChangeSetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    working_copy_id: uuid.UUID
    base_snapshot_id: uuid.UUID
    state: Literal["open", "applied", "discarded", "superseded", "conflicted"]
    added_count: int
    modified_count: int
    deleted_count: int
    artifact_count: int
    changed_bytes: int
    diff_bytes: int
    truncated: bool
    entries: list[ChangeSetEntrySummary]
    returned_count: int
    created_at: datetime.datetime


class ArtifactOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: Literal["file", "log", "report"]
    size_bytes: int
    mime: str | None
    retention: Literal["ephemeral", "retained", "expired"]
    created_at: datetime.datetime


class CheckpointSpec(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    note: str | None = None


class ChangeSetApply(BaseModel):
    selected_entry_ids: list[uuid.UUID] | None = None
    checkpoint: CheckpointSpec | None = None


class SaveConflict(BaseModel):
    error: Literal["head_moved"] = "head_moved"
    base_snapshot_id: uuid.UUID
    current_snapshot_id: uuid.UUID
    message: str


class ArtifactExport(BaseModel):
    drive_folder_id: uuid.UUID | None = None
    name: str | None = None


def _artifact_out(art: ProjectArtifact) -> ArtifactOut:
    return ArtifactOut(
        id=art.id,
        name=art.name,
        kind=art.kind,  # type: ignore[arg-type]
        size_bytes=art.size_bytes,
        mime=art.mime,
        retention=art.retention,  # type: ignore[arg-type]
        created_at=art.created_at,
    )


async def _sandbox_state(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> SandboxRunState | None:
    sr = await db.scalar(
        select(ProjectSandboxRun)
        .where(
            ProjectSandboxRun.tenant_id == ctx.tenant_id,
            ProjectSandboxRun.working_copy_id == wc.id,
        )
        .order_by(ProjectSandboxRun.created_at.desc())
        .limit(1)
    )
    if sr is None:
        return None
    return SandboxRunState(
        run_id=sr.run_id,
        state=sr.state,  # type: ignore[arg-type]
        warm=sr.warm_until is not None,
        exit_code=sr.exit_code,
        timed_out=sr.timed_out,
        termination_reason=sr.termination_reason,
    )


async def _wc_summary(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> WorkingCopySummary:
    project = await db.get(Project, (ctx.tenant_id, wc.project_id))
    moved = bool(project is not None and wc_svc.head_moved(project, wc))
    open_cs = await changes_svc.open_change_set(db, ctx, wc)
    sandbox = await _sandbox_state(db, ctx, wc)
    return WorkingCopySummary(
        id=wc.id,
        project_id=wc.project_id,
        session_id=wc.session_id,
        base_snapshot_id=wc.base_snapshot_id,
        state=wc.state,  # type: ignore[arg-type]
        overlay_entry_count=wc.overlay_entry_count,
        overlay_bytes=wc.overlay_bytes,
        reserved_bytes=wc.reserved_bytes,
        head_moved=moved,
        open_change_set_id=open_cs.id if open_cs is not None else None,
        sandbox=sandbox,
        last_boundary_at=wc.last_boundary_at,
        expires_at=wc.expires_at,
        updated_at=wc.updated_at,
    )


@router.get("/sessions/{session_id}/working-copy")
async def get_working_copy(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> WorkingCopySummary | None:
    cc = _caller(ctx)
    wc = await wc_svc.get_live(db, cc, session_id=session_id)
    if wc is None:
        return None
    return await _wc_summary(db, cc, wc)


@router.post("/projects/{project_id}/sandbox-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_sandbox_run(
    project_id: uuid.UUID,
    body: SandboxRunRequest,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
    session_id: Annotated[uuid.UUID, Query()],
) -> SandboxRunState:
    cc = _caller(ctx)
    try:
        wc = await wc_svc.open_working_copy(db, cc, session_id=session_id)
        if wc.project_id != project_id:
            raise HTTPException(status_code=404, detail="not_found")
        outcome = await sbx_svc.run_sandbox(
            db,
            cc,
            wc,
            run_id=uuid.uuid4(),
            request=sbx_svc.SandboxRequest(command=body.command),
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    sr = outcome.sandbox_run
    return SandboxRunState(
        run_id=sr.run_id,
        state=sr.state,  # type: ignore[arg-type]
        warm=sr.warm_until is not None,
        exit_code=sr.exit_code,
        timed_out=sr.timed_out,
        termination_reason=sr.termination_reason,
    )


@router.get("/projects/{project_id}/working-copies/{wc_id}")
async def get_working_copy_by_id(
    project_id: uuid.UUID,
    wc_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> WorkingCopySummary:
    cc = _caller(ctx)
    try:
        wc = await wc_svc.get_by_id(db, cc, project_id=project_id, wc_id=wc_id)
    except ServiceError as e:
        raise _http(e) from None
    return await _wc_summary(db, cc, wc)


@router.get("/projects/{project_id}/change-sets/{cs_id}")
async def get_change_set(
    project_id: uuid.UUID,
    cs_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> ChangeSetOut:
    cc = _caller(ctx)
    try:
        cs = await changes_svc.get_change_set(db, cc, project_id=project_id, cs_id=cs_id)
        entries, returned = await changes_svc.get_change_set_entries(
            db, cc, cs, cursor=cursor, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None
    return ChangeSetOut(
        id=cs.id,
        project_id=cs.project_id,
        working_copy_id=cs.working_copy_id,
        base_snapshot_id=cs.base_snapshot_id,
        state=cs.state,  # type: ignore[arg-type]
        added_count=cs.added_count,
        modified_count=cs.modified_count,
        deleted_count=cs.deleted_count,
        artifact_count=cs.artifact_count,
        changed_bytes=cs.changed_bytes,
        diff_bytes=cs.diff_bytes,
        truncated=cs.truncated,
        entries=[
            ChangeSetEntrySummary(
                id=e.id,
                path=e.path,
                change_kind=e.change_kind,  # type: ignore[arg-type]
                size_bytes=e.size_bytes,
                executable=e.executable,
                is_binary=e.is_binary,
                has_diff=e.diff_object_key is not None,
                diff_truncated=e.diff_truncated,
                selected=e.selected,
            )
            for e in entries
        ],
        returned_count=returned,
        created_at=cs.created_at,
    )


@router.get(
    "/projects/{project_id}/change-sets/{cs_id}/entries/{entry_id}/diff",
    response_class=PlainTextResponse,
)
async def get_change_set_diff(
    project_id: uuid.UUID,
    cs_id: uuid.UUID,
    entry_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> str:
    try:
        return await changes_svc.get_entry_diff(
            db, _caller(ctx), project_id=project_id, cs_id=cs_id, entry_id=entry_id
        )
    except ServiceError as e:
        raise _http(e) from None


@router.post("/projects/{project_id}/change-sets/{cs_id}/apply")
async def apply_change_set(
    project_id: uuid.UUID,
    cs_id: uuid.UUID,
    body: ChangeSetApply,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectSummary:
    from app.services.errors import Conflict as _Conflict

    cc = _caller(ctx)
    try:
        await changes_svc.apply_change_set(
            db,
            cc,
            project_id=project_id,
            cs_id=cs_id,
            selected_entry_ids=body.selected_entry_ids,
            checkpoint_name=(body.checkpoint.name if body.checkpoint else None),
        )
        item = await svc.get_list_item(db, cc, project_id=project_id)
        src = await svc.get_source(db, cc, project_id=project_id)
        await db.commit()
    except _Conflict as e:
        await db.rollback()
        if e.message == "head_moved":
            # Rebuild the conflict envelope from the (now reloaded) working copy + head.
            body_out = await _save_conflict(db, cc, project_id=project_id, cs_id=cs_id)
            raise HTTPException(status_code=409, detail=body_out.model_dump(mode="json")) from None
        raise _http(e) from None
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return _summary(item, src)


async def _save_conflict(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, cs_id: uuid.UUID
) -> SaveConflict:
    cs = await db.get(ProjectChangeSet, (ctx.tenant_id, cs_id))
    project = await db.get(Project, (ctx.tenant_id, project_id))
    base = cs.base_snapshot_id if cs is not None else project_id
    current = project.current_snapshot_id if project and project.current_snapshot_id else base
    return SaveConflict(
        base_snapshot_id=base,
        current_snapshot_id=current,
        message="the project head moved since this working copy opened; review the rebased changes",
    )


@router.post("/projects/{project_id}/change-sets/{cs_id}/discard")
async def discard_change_set(
    project_id: uuid.UUID,
    cs_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> WorkingCopySummary:
    cc = _caller(ctx)
    try:
        wc = await changes_svc.discard_change_set(db, cc, project_id=project_id, cs_id=cs_id)
        out = await _wc_summary(db, cc, wc)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return out


@router.post("/projects/{project_id}/working-copies/{wc_id}/discard")
async def discard_working_copy(
    project_id: uuid.UUID,
    wc_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> WorkingCopySummary:
    cc = _caller(ctx)
    try:
        wc = await changes_svc.discard_working_copy(db, cc, project_id=project_id, wc_id=wc_id)
        out = await _wc_summary(db, cc, wc)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return out


@router.get("/projects/{project_id}/artifacts")
async def list_artifacts(
    project_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    working_copy_id: uuid.UUID | None = None,
) -> list[ArtifactOut]:
    try:
        arts = await changes_svc.list_artifacts(
            db, _caller(ctx), project_id=project_id, working_copy_id=working_copy_id
        )
    except ServiceError as e:
        raise _http(e) from None
    return [_artifact_out(a) for a in arts]


@router.post("/projects/{project_id}/artifacts/{art_id}/keep")
async def keep_artifact(
    project_id: uuid.UUID,
    art_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ArtifactOut:
    try:
        art = await changes_svc.keep_artifact(
            db, _caller(ctx), project_id=project_id, artifact_id=art_id
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return _artifact_out(art)


@router.post(
    "/projects/{project_id}/artifacts/{art_id}/export", status_code=status.HTTP_201_CREATED
)
async def export_artifact(
    project_id: uuid.UUID,
    art_id: uuid.UUID,
    body: ArtifactExport,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DriveNode:
    try:
        node = await changes_svc.export_artifact(
            db,
            _caller(ctx),
            project_id=project_id,
            artifact_id=art_id,
            drive_folder_id=body.drive_folder_id,
            name=body.name,
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return _node(node)
