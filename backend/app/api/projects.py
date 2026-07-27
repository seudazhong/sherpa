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
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue
from app.api.schemas import SessionSummary
from app.api.sessions import _summary as _session_summary
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import ProjectSource
from app.services import CallerContext, ServiceError
from app.services import github_source as gh
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


class ProjectSnapshotOut(BaseModel):
    id: uuid.UUID
    reason: str
    entry_count: int
    size_bytes: int
    pinned: bool
    created_at: datetime.datetime


class ProjectChatCreate(BaseModel):
    title: str | None = None


class ProjectContextOut(BaseModel):
    session_id: uuid.UUID
    project_id: uuid.UUID | None
    project_name: str | None
    bound: bool


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
    except ServiceError as e:
        raise _http(e) from None
    return ProjectContextOut(
        session_id=pc.session_id,
        project_id=pc.project_id,
        project_name=pc.project_name,
        bound=pc.bound,
    )
