"""Workspace Projects capability layer (ADR-037, W2a).

A Project is a named, durable development state. W2a creation paths — **blank**,
**template**, **archive import** — all produce a single initial ``reason='import'``
immutable snapshot. Snapshots reference the same ADR-030 content-addressed, deduped,
ref-counted ``storage_blobs`` as Drive (shared per-user quota; unchanged bytes never
multiply). Archive import is a durable job (:mod:`app.services.projects_import`); this
module owns synchronous create + read + the shared snapshot materializer + Project-bound
Chat (``sessions.project_id``, immutable after the first admitted message).

W2a is read/discuss only: **no** working copy, sandbox, save, or GitHub (those are
W2b/W3/W4). Every query is scoped by ``tenant_id`` AND ``user_id`` (ADR-015/029).
The caller owns the transaction (services flush, never commit).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Project,
    ProjectImportJob,
    ProjectSnapshot,
    ProjectSnapshotEntry,
    ProjectSource,
    StorageBlob,
)
from app.models import Session as SessionModel
from app.services import drive as drive_svc
from app.services.archive import ArchiveEntry
from app.services.context import CallerContext
from app.services.errors import Conflict, InsufficientStorage, Invalid, NotFound

_LIST_LIMIT = 100
_TREE_LIMIT = 500


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("projects require a user context")
    return ctx.user_id


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 200:
        raise Invalid("project name must be 1..200 characters")
    return cleaned


# --- built-in templates -----------------------------------------------------
# W2a templates are small, server-defined starter file sets. A template project
# copies these entries and severs any template history (a fresh import snapshot).

TEMPLATES: dict[str, list[ArchiveEntry]] = {
    "notes": [
        ArchiveEntry(
            path="README.md",
            entry_kind="file",
            data=b"# Notes\n\nA blank notes project. Add your files in Drive-style structure.\n",
        ),
        ArchiveEntry(
            path="notes/todo.md",
            entry_kind="file",
            data=b"# TODO\n\n- [ ] first task\n",
        ),
    ],
    "python-basic": [
        ArchiveEntry(
            path="README.md",
            entry_kind="file",
            data=b"# Python basic\n\nA minimal Python starter project.\n",
        ),
        ArchiveEntry(
            path="main.py",
            entry_kind="file",
            data=b'def main() -> None:\n    print("hello, sherpa")\n\n\n'
            b'if __name__ == "__main__":\n    main()\n',
        ),
        ArchiveEntry(path="requirements.txt", entry_kind="file", data=b""),
    ],
}


def list_templates() -> list[dict[str, str]]:
    return [
        {"id": "notes", "name": "Notes", "description": "A README + notes folder."},
        {
            "id": "python-basic",
            "name": "Python basic",
            "description": "A minimal Python starter (main.py + requirements).",
        },
    ]


# --- snapshot materializer (shared by blank/template/archive) ----------------


def _dir_parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts) - 1)]


async def _recompute_project_used(db: AsyncSession, project: Project) -> int:
    """Project rollup: sum of DISTINCT blob sizes referenced by any of the project's
    snapshots (each distinct blob charged once), reusing the shared storage_blobs."""
    distinct_hashes = (
        select(ProjectSnapshotEntry.content_hash)
        .join(
            ProjectSnapshot,
            (ProjectSnapshot.tenant_id == ProjectSnapshotEntry.tenant_id)
            & (ProjectSnapshot.id == ProjectSnapshotEntry.snapshot_id),
        )
        .where(
            ProjectSnapshot.tenant_id == project.tenant_id,
            ProjectSnapshot.project_id == project.id,
            ProjectSnapshotEntry.content_hash.is_not(None),
        )
        .distinct()
        .subquery()
    )
    total = await db.scalar(
        select(func.coalesce(func.sum(StorageBlob.size_bytes), 0)).where(
            StorageBlob.tenant_id == project.tenant_id,
            StorageBlob.user_id == project.user_id,
            StorageBlob.content_hash.in_(select(distinct_hashes.c.content_hash)),
        )
    )
    project.used_bytes = int(total or 0)
    return project.used_bytes


async def build_import_snapshot(
    db: AsyncSession,
    ctx: CallerContext,
    project: Project,
    entries: list[ArchiveEntry],
    *,
    source_oid: str | None = None,
) -> ProjectSnapshot:
    """Materialize ``entries`` into a new immutable ``reason='import'`` snapshot and
    atomically point ``project.current_snapshot_id`` at it. Files reference the shared
    deduped blob store; parent dirs are synthesized. ``source_oid`` records the GitHub
    commit OID for W2b imports (NULL for blank/template/archive). Raises
    InsufficientStorage over quota. Idempotent per project: only ever the initial
    snapshot in W2a/W2b."""
    uid = project.user_id

    # 1) content-address file bytes (dedup within the batch) + quota check up front.
    file_hashes: dict[str, bytes] = {}
    unique: dict[bytes, bytes] = {}
    for e in entries:
        if e.entry_kind == "file":
            data = e.data or b""
            h = hashlib.sha256(data).digest()
            file_hashes[e.path] = h
            unique.setdefault(h, data)

    account = await drive_svc.get_account(db, ctx, uid)
    new_bytes = 0
    for h, data in unique.items():
        existing = await db.get(StorageBlob, (ctx.tenant_id, uid, h))
        if existing is None or existing.ref_count == 0:
            new_bytes += len(data)
    if account.used_bytes + account.reserved_bytes + new_bytes > account.quota_bytes:
        raise InsufficientStorage("quota exceeded")

    # 2) write blobs (object written before commit; ref_count recomputed after).
    for _h, data in unique.items():
        await drive_svc.ensure_blob(
            db, ctx, uid, data=data, content_type="application/octet-stream"
        )

    # 3) build the full entry set (synthesize parent dirs, dedup paths).
    dir_paths: set[str] = set()
    explicit: dict[str, ArchiveEntry] = {}
    for e in entries:
        if e.entry_kind == "dir":
            dir_paths.add(e.path)
        else:
            explicit[e.path] = e
        for parent in _dir_parents(e.path):
            dir_paths.add(parent)

    snapshot = ProjectSnapshot(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=project.id,
        parent_id=project.current_snapshot_id,
        reason="import",
        entry_count=0,
        size_bytes=0,
        source_oid=source_oid,
    )
    db.add(snapshot)
    await db.flush()

    entry_count = 0
    distinct_sizes: dict[bytes, int] = {}
    for dpath in sorted(dir_paths):
        db.add(
            ProjectSnapshotEntry(
                tenant_id=ctx.tenant_id,
                id=uuid.uuid4(),
                snapshot_id=snapshot.id,
                user_id=uid,
                path=dpath,
                entry_kind="dir",
            )
        )
        entry_count += 1
    for path, e in explicit.items():
        if e.entry_kind == "file":
            h = file_hashes[path]
            size = len(unique[h])
            distinct_sizes[h] = size
            db.add(
                ProjectSnapshotEntry(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    snapshot_id=snapshot.id,
                    user_id=uid,
                    path=path,
                    entry_kind="file",
                    content_hash=h,
                    size_bytes=size,
                    executable=e.executable,
                )
            )
        else:  # symlink
            db.add(
                ProjectSnapshotEntry(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    snapshot_id=snapshot.id,
                    user_id=uid,
                    path=path,
                    entry_kind="symlink",
                    symlink_target=e.symlink_target,
                )
            )
        entry_count += 1

    snapshot.entry_count = entry_count
    snapshot.size_bytes = sum(distinct_sizes.values())
    await db.flush()

    # 4) recompute shared blob ref-counts + account usage now that entries exist.
    for h in unique:
        await drive_svc.recompute_blob(db, ctx, uid, h)
    await drive_svc.recompute_used(db, ctx, uid)

    # 5) atomically advance the head + rollups.
    project.current_snapshot_id = snapshot.id
    project.last_activity_at = _now()
    await _recompute_project_used(db, project)
    await db.flush()
    return snapshot


# --- create (blank / template) ----------------------------------------------


async def _assert_name_free(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, name: str
) -> None:
    existing = await db.scalar(
        select(Project.id).where(
            Project.tenant_id == ctx.tenant_id,
            Project.user_id == uid,
            Project.name == name,
            Project.status != "deleting",
        )
    )
    if existing is not None:
        raise Conflict("a project with that name already exists")


async def create_project(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    name: str,
    description: str | None = None,
    template_id: str | None = None,
) -> Project:
    """Create a blank (``template_id=None``) or template project. Single transaction:
    the initial ``import`` snapshot is built synchronously (no staging)."""
    uid = _require_user(ctx)
    name = _validate_name(name)
    if template_id is not None and template_id not in TEMPLATES:
        raise Invalid(f"unknown template: {template_id}")
    await _assert_name_free(db, ctx, uid, name)

    project = Project(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        name=name,
        description=(description or None),
        status="active",
        source_status="unbound",
        last_activity_at=_now(),
    )
    db.add(project)
    await db.flush()

    entries = TEMPLATES.get(template_id, []) if template_id is not None else []
    await build_import_snapshot(db, ctx, project, list(entries))
    return project


# --- read -------------------------------------------------------------------


async def get_project(db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID) -> Project:
    uid = _require_user(ctx)
    project = await db.get(Project, (ctx.tenant_id, project_id))
    if project is None or project.user_id != uid or project.status == "deleting":
        raise NotFound("project not found")
    return project


@dataclasses.dataclass(frozen=True)
class ProjectListItem:
    project: Project
    import_status: str  # none | importing | ready | failed
    import_failure_reason: str | None


async def _import_status(db: AsyncSession, project: Project) -> tuple[str, str | None]:
    if project.current_snapshot_id is not None:
        return "ready", None
    job = await db.scalar(
        select(ProjectImportJob)
        .where(
            ProjectImportJob.tenant_id == project.tenant_id,
            ProjectImportJob.project_id == project.id,
        )
        .order_by(ProjectImportJob.created_at.desc())
        .limit(1)
    )
    if job is None:
        return "none", None
    if job.stage == "failed":
        return "failed", job.termination_reason
    return "importing", None


async def list_projects(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    query: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[ProjectListItem]:
    uid = _require_user(ctx)
    limit = max(1, min(limit, _LIST_LIMIT))
    stmt = select(Project).where(
        Project.tenant_id == ctx.tenant_id,
        Project.user_id == uid,
        Project.status != "deleting",
    )
    if status in ("active", "archived"):
        stmt = stmt.where(Project.status == status)
    if query and query.strip():
        stmt = stmt.where(Project.name.ilike(f"%{query.strip()}%"))
    activity = func.coalesce(Project.last_activity_at, Project.created_at)
    stmt = stmt.order_by(activity.desc(), Project.id.desc()).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    items: list[ProjectListItem] = []
    for p in rows:
        st, reason = await _import_status(db, p)
        items.append(ProjectListItem(project=p, import_status=st, import_failure_reason=reason))
    return items


async def get_list_item(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID
) -> ProjectListItem:
    project = await get_project(db, ctx, project_id=project_id)
    st, reason = await _import_status(db, project)
    return ProjectListItem(project=project, import_status=st, import_failure_reason=reason)


async def get_source(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID
) -> ProjectSource | None:
    """The GitHub source provenance (W2b) for a project, or None (blank/template/archive).
    Never exposes any credential — provenance only."""
    project = await get_project(db, ctx, project_id=project_id)
    return await db.scalar(
        select(ProjectSource).where(
            ProjectSource.tenant_id == ctx.tenant_id,
            ProjectSource.project_id == project.id,
        )
    )


@dataclasses.dataclass(frozen=True)
class TreeEntry:
    path: str
    entry_kind: str
    size_bytes: int
    executable: bool


@dataclasses.dataclass(frozen=True)
class ProjectTree:
    project_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    entries: list[TreeEntry]
    truncated: bool = False


async def get_tree(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
    path: str | None = None,
    limit: int = 200,
) -> ProjectTree:
    """Bounded page of a snapshot's entries (defaults to the head snapshot).

    Fetches ``limit + 1`` rows so the caller can tell whether the page is
    ``truncated`` (more entries exist beyond it) without a second count query.
    """
    project = await get_project(db, ctx, project_id=project_id)
    limit = max(1, min(limit, _TREE_LIMIT))
    snap_id = snapshot_id or project.current_snapshot_id
    if snap_id is None:
        return ProjectTree(project_id=project.id, snapshot_id=None, entries=[], truncated=False)
    snapshot = await db.get(ProjectSnapshot, (ctx.tenant_id, snap_id))
    if snapshot is None or snapshot.project_id != project.id:
        raise NotFound("snapshot not found")
    stmt = select(ProjectSnapshotEntry).where(
        ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
        ProjectSnapshotEntry.snapshot_id == snap_id,
    )
    if path and path.strip():
        prefix = path.strip().strip("/")
        stmt = stmt.where(ProjectSnapshotEntry.path.like(f"{prefix}/%"))
    stmt = stmt.order_by(ProjectSnapshotEntry.path).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    entries = [
        TreeEntry(
            path=r.path,
            entry_kind=r.entry_kind,
            size_bytes=r.size_bytes,
            executable=r.executable,
        )
        for r in rows
    ]
    return ProjectTree(
        project_id=project.id, snapshot_id=snap_id, entries=entries, truncated=truncated
    )


async def list_snapshots(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, limit: int = 20
) -> list[ProjectSnapshot]:
    project = await get_project(db, ctx, project_id=project_id)
    rows = (
        (
            await db.execute(
                select(ProjectSnapshot)
                .where(
                    ProjectSnapshot.tenant_id == ctx.tenant_id,
                    ProjectSnapshot.project_id == project.id,
                )
                .order_by(ProjectSnapshot.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def read_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    project_id: uuid.UUID,
    path: str,
    snapshot_id: uuid.UUID | None = None,
    max_bytes: int = 200_000,
) -> tuple[ProjectSnapshotEntry, bytes]:
    """Read a file entry's bytes from a snapshot (untrusted content, ADR-009)."""
    project = await get_project(db, ctx, project_id=project_id)
    snap_id = snapshot_id or project.current_snapshot_id
    if snap_id is None:
        raise NotFound("no snapshot")
    norm = (path or "").strip().strip("/")
    entry = await db.scalar(
        select(ProjectSnapshotEntry).where(
            ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
            ProjectSnapshotEntry.snapshot_id == snap_id,
            ProjectSnapshotEntry.path == norm,
        )
    )
    if entry is None:
        raise NotFound("path not found")
    if entry.entry_kind != "file" or entry.content_hash is None:
        raise Invalid("not a file")
    blob = await db.get(StorageBlob, (ctx.tenant_id, project.user_id, entry.content_hash))
    if blob is None:
        raise NotFound("blob missing")
    from app.files import build_object_store

    data = await build_object_store().get(blob.object_key)
    return entry, data[:max_bytes]


# --- Project-bound Chat (sessions.project_id) -------------------------------


async def open_in_chat(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, title: str | None = None
) -> SessionModel:
    """Create a NEW Project-bound web chat session. The binding is set at creation and
    is immutable after the first admitted user message; switching Project = a new chat.

    A Project with no head snapshot (still importing, or a ``failed`` import that never
    activated bytes — ADR-037) has nothing to read/discuss, so binding a chat to it is
    meaningless. Refuse deterministically (422) as the backend defense behind the UI,
    which also hides the control for such projects."""
    project = await get_project(db, ctx, project_id=project_id)
    if project.current_snapshot_id is None:
        raise Invalid("project has no head snapshot; import must finish before opening in chat")
    session_id = uuid.uuid4()
    session = SessionModel(
        tenant_id=ctx.tenant_id,
        id=session_id,
        user_id=project.user_id,
        umo_key=f"web:chat:{session_id}",
        channel="web",
        channel_installation_id="local",
        scope_type="chat",
        external_scope_id=str(session_id),
        status="open",
        title=title,
        project_id=project.id,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session, ["created_at", "updated_at"])
    return session


@dataclasses.dataclass(frozen=True)
class ProjectContext:
    session_id: uuid.UUID
    project_id: uuid.UUID | None
    project_name: str | None
    bound: bool


async def project_context(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> ProjectContext:
    uid = _require_user(ctx)
    session = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if session is None or session.user_id != uid or session.status == "deleted":
        raise NotFound("session not found")
    name: str | None = None
    if session.project_id is not None:
        project = await db.get(Project, (ctx.tenant_id, session.project_id))
        name = project.name if project is not None else None
    bound = session.project_id is not None and session.admitted_seq is not None
    return ProjectContext(
        session_id=session.id,
        project_id=session.project_id,
        project_name=name,
        bound=bound,
    )
