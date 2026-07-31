"""Workspace Projects W3 change-set projection + review actions (ADR-040).

A **change set** is a bounded, reviewable projection of a working copy's overlay vs its base
snapshot (``project_change_sets`` + ``project_change_set_entries``): added/modified/deleted
files, per-file bounded unified diffs (spilled to MinIO), binary detection, and run
artifacts. It is durable so review/apply/discard are stable across reloads.

Review actions:

* **Save selected / Save + checkpoint** — apply the selected subset via
  :func:`app.services.project_workcopy.save` (a head-generation compare-and-set; a moved head
  raises ``Conflict('head_moved')`` → ``409 SaveConflict``). Human review gate — never an
  agent tool.
* **Discard** — drop the overlay/staged bytes (head byte-identical to the base).
* **Artifacts** — run outputs (logs/reports) are ephemeral and charge **no** quota until
  **Keep** (retained → counted) or **Export** (copied into Drive).

The caller owns the transaction (services flush, never commit).
"""

from __future__ import annotations

import dataclasses
import datetime
import difflib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    DriveNode,
    Project,
    ProjectArtifact,
    ProjectChangeSet,
    ProjectChangeSetEntry,
    ProjectSnapshotEntry,
    ProjectWorkingCopy,
    StorageBlob,
)
from app.objectstore import build_object_store
from app.services import drive as drive_svc
from app.services import project_workcopy as wc_svc
from app.services.context import CallerContext
from app.services.errors import Conflict, InsufficientStorage, Invalid, NotFound, TooLarge

_ENTRY_PAGE = 200

#: How much of an object is inspected to classify it as binary. The same heuristic the
#: in-memory path uses, but as a **bounded prefix read** so classifying a 500 MiB object
#: costs 8 KiB rather than 500 MiB (config §1.7 peak model).
_BINARY_SNIFF_BYTES = 8192


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


async def _read_blob(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, content_hash: bytes | None
) -> bytes:
    if content_hash is None:
        return b""
    blob = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    if blob is None:
        return b""
    return await build_object_store().get(blob.object_key)


async def _blob_size(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, content_hash: bytes | None
) -> int:
    """The recorded size of a blob — from the row, never by reading the object."""
    if content_hash is None:
        return 0
    blob = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    return int(blob.size_bytes) if blob is not None else 0


async def _sniff_binary(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, content_hash: bytes | None
) -> bool:
    """Classify an object as binary from a **bounded prefix**, never a full read.

    A 500 MiB object must not be pulled into the worker just to decide it will not be
    diffed. A NUL in the first few KiB is the same heuristic :func:`_is_binary` applies to
    an in-memory payload; reading only that prefix makes the cost constant."""
    if content_hash is None:
        return False
    blob = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    if blob is None:
        return False
    store = build_object_store()
    try:
        prefix = await store.get_prefix(blob.object_key, _BINARY_SNIFF_BYTES)
    except Exception:  # noqa: BLE001 - a classification read must never fail the projection
        return False
    return b"\x00" in prefix


async def _base_file_hashes(
    db: AsyncSession, ctx: CallerContext, snapshot_id: uuid.UUID
) -> dict[str, tuple[bytes | None, int]]:
    rows = (
        await db.execute(
            select(
                ProjectSnapshotEntry.path,
                ProjectSnapshotEntry.content_hash,
                ProjectSnapshotEntry.size_bytes,
            ).where(
                ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
                ProjectSnapshotEntry.snapshot_id == snapshot_id,
                ProjectSnapshotEntry.entry_kind == "file",
            )
        )
    ).all()
    return {r.path: (r.content_hash, r.size_bytes) for r in rows}


# --- build the reviewable projection ----------------------------------------


async def build_change_set(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    run_id: uuid.UUID | None = None,
) -> ProjectChangeSet | None:
    """(Re)build the current reviewable change set from the working copy's overlay. Prior
    open change sets for this working copy are superseded. Returns None if the overlay is
    empty. Bounded by ``WORKING_COPY_MAX_CHANGED_FILES`` (over ⇒ ``truncated`` = explicit
    partial). Per-file unified diffs are spilled to MinIO, bounded by
    ``WORKING_COPY_MAX_DIFF_BYTES``; binary files carry ``is_binary`` and no inline diff."""
    overlay = (
        (
            await db.execute(
                select(wc_svc.ProjectWorkingCopyEntry)
                .where(
                    wc_svc.ProjectWorkingCopyEntry.tenant_id == ctx.tenant_id,
                    wc_svc.ProjectWorkingCopyEntry.working_copy_id == wc.id,
                )
                .order_by(wc_svc.ProjectWorkingCopyEntry.path)
            )
        )
        .scalars()
        .all()
    )

    # Supersede prior open change sets for this working copy.
    prior = (
        (
            await db.execute(
                select(ProjectChangeSet).where(
                    ProjectChangeSet.tenant_id == ctx.tenant_id,
                    ProjectChangeSet.working_copy_id == wc.id,
                    ProjectChangeSet.state == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    for p in prior:
        p.state = "superseded"
    await db.flush()

    if not overlay:
        return None

    base = await _base_file_hashes(db, ctx, wc.base_snapshot_id)
    cs = ProjectChangeSet(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=wc.project_id,
        working_copy_id=wc.id,
        session_id=wc.session_id,
        run_id=run_id,
        base_snapshot_id=wc.base_snapshot_id,
        fence_token=wc.fence_token,
        state="open",
    )
    db.add(cs)
    await db.flush()

    max_files = settings.working_copy_max_changed_files
    diff_cap = settings.working_copy_max_diff_bytes
    added = modified = deleted = 0
    changed_bytes = 0
    diff_bytes = 0
    truncated = False

    for o in overlay:
        if added + modified + deleted >= max_files:
            truncated = True
            break
        old_hash = base.get(o.path, (None, 0))[0]
        old_size = base.get(o.path, (None, 0))[1]
        if o.change_kind == "added":
            added += 1
            new_hash = o.content_hash
            size = o.size_bytes
            changed_bytes += size
        elif o.change_kind == "modified":
            modified += 1
            new_hash = o.content_hash
            size = o.size_bytes
            changed_bytes += size
        else:  # deleted
            deleted += 1
            new_hash = None
            size = 0

        # Decide DIFFABILITY FROM RECORDED SIZES, before touching the object store. Reading
        # a 500 MiB object in order to conclude "too big to diff" is exactly the mistake
        # this ordering exists to prevent: sizes are already on the rows (the snapshot entry
        # for the old side, the overlay entry for the new side).
        new_size = o.size_bytes if new_hash is not None else 0
        if old_hash is not None and old_size <= 0:
            old_size = await _blob_size(db, ctx, wc.user_id, old_hash)
        oversized = new_size > diff_cap or old_size > diff_cap

        if oversized:
            # Bounded prefix reads only — enough to classify, never the whole object.
            is_binary = await _sniff_binary(db, ctx, wc.user_id, new_hash) or await _sniff_binary(
                db, ctx, wc.user_id, old_hash
            )
            db.add(
                ProjectChangeSetEntry(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    change_set_id=cs.id,
                    path=o.path,
                    change_kind=o.change_kind,
                    old_content_hash=old_hash,
                    new_content_hash=new_hash,
                    size_bytes=size,
                    executable=o.executable,
                    is_binary=is_binary,
                    diff_object_key=None,
                    # Truthful: there IS more to show, we declined to render it. The UI
                    # already treats this as "diff not shown", which is what happened.
                    diff_truncated=not is_binary,
                    selected=True,
                )
            )
            continue

        new_bytes = await _read_blob(db, ctx, wc.user_id, new_hash)
        old_bytes = await _read_blob(db, ctx, wc.user_id, old_hash)
        is_binary = _is_binary(new_bytes) or _is_binary(old_bytes)

        diff_key: str | None = None
        diff_trunc = False
        if not is_binary:
            entry_id = uuid.uuid4()
            diff_text = "".join(
                difflib.unified_diff(
                    old_bytes.decode("utf-8", "replace").splitlines(keepends=True),
                    new_bytes.decode("utf-8", "replace").splitlines(keepends=True),
                    fromfile=f"a/{o.path}",
                    tofile=f"b/{o.path}",
                )
            )
            encoded = diff_text.encode("utf-8", "replace")
            if len(encoded) > diff_cap:
                encoded = encoded[:diff_cap]
                diff_trunc = True
            if encoded:
                diff_key = f"project-diff/{cs.id}/{entry_id}"
                await build_object_store().put(diff_key, encoded, "text/plain")
                diff_bytes += len(encoded)
            db.add(
                ProjectChangeSetEntry(
                    tenant_id=ctx.tenant_id,
                    id=entry_id,
                    change_set_id=cs.id,
                    path=o.path,
                    change_kind=o.change_kind,
                    old_content_hash=old_hash,
                    new_content_hash=new_hash,
                    size_bytes=size,
                    executable=o.executable,
                    is_binary=False,
                    diff_object_key=diff_key,
                    diff_truncated=diff_trunc,
                    selected=True,
                )
            )
        else:
            db.add(
                ProjectChangeSetEntry(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    change_set_id=cs.id,
                    path=o.path,
                    change_kind=o.change_kind,
                    old_content_hash=old_hash,
                    new_content_hash=new_hash,
                    size_bytes=size,
                    executable=o.executable,
                    is_binary=is_binary,
                    diff_object_key=None,
                    diff_truncated=True if not is_binary else False,
                    selected=True,
                )
            )

        # Release this file's bytes before the next iteration: the projection holds one
        # under-cap pair at a time, never an accumulation across the change set.
        del new_bytes, old_bytes

    artifact_count = await db.scalar(
        select(func.count())
        .select_from(ProjectArtifact)
        .where(
            ProjectArtifact.tenant_id == ctx.tenant_id,
            ProjectArtifact.working_copy_id == wc.id,
        )
    )
    cs.added_count = added
    cs.modified_count = modified
    cs.deleted_count = deleted
    cs.artifact_count = int(artifact_count or 0)
    cs.changed_bytes = changed_bytes
    cs.diff_bytes = diff_bytes
    cs.truncated = truncated
    await db.flush()
    return cs


async def open_change_set(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> ProjectChangeSet | None:
    return await db.scalar(
        select(ProjectChangeSet)
        .where(
            ProjectChangeSet.tenant_id == ctx.tenant_id,
            ProjectChangeSet.working_copy_id == wc.id,
            ProjectChangeSet.state == "open",
        )
        .order_by(ProjectChangeSet.created_at.desc())
        .limit(1)
    )


# --- read -------------------------------------------------------------------


async def get_change_set(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, cs_id: uuid.UUID
) -> ProjectChangeSet:
    cs = await db.get(ProjectChangeSet, (ctx.tenant_id, cs_id))
    if cs is None or cs.project_id != project_id:
        raise NotFound("change set not found")
    project = await db.get(Project, (ctx.tenant_id, project_id))
    if project is None or project.user_id != ctx.user_id:
        raise NotFound("project not found")
    return cs


async def get_change_set_entries(
    db: AsyncSession,
    ctx: CallerContext,
    cs: ProjectChangeSet,
    *,
    cursor: str | None = None,
    limit: int = _ENTRY_PAGE,
) -> tuple[list[ProjectChangeSetEntry], int]:
    limit = max(1, min(limit, _ENTRY_PAGE))
    stmt = select(ProjectChangeSetEntry).where(
        ProjectChangeSetEntry.tenant_id == ctx.tenant_id,
        ProjectChangeSetEntry.change_set_id == cs.id,
    )
    if cursor:
        stmt = stmt.where(ProjectChangeSetEntry.path > cursor)
    stmt = stmt.order_by(ProjectChangeSetEntry.path).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, len(rows)


async def get_entry_diff(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    project_id: uuid.UUID,
    cs_id: uuid.UUID,
    entry_id: uuid.UUID,
) -> str:
    cs = await get_change_set(db, ctx, project_id=project_id, cs_id=cs_id)
    entry = await db.get(ProjectChangeSetEntry, (ctx.tenant_id, entry_id))
    if entry is None or entry.change_set_id != cs.id:
        raise NotFound("entry not found")
    if entry.is_binary or entry.diff_object_key is None:
        raise TooLarge("no inline diff (binary or over cap) — download only")
    return (await build_object_store().get(entry.diff_object_key)).decode("utf-8", "replace")


# --- apply / discard --------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ApplyResult:
    project: Project
    change_set: ProjectChangeSet
    new_open_change_set_id: uuid.UUID | None


async def apply_change_set(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    project_id: uuid.UUID,
    cs_id: uuid.UUID,
    selected_entry_ids: list[uuid.UUID] | None = None,
    checkpoint_name: str | None = None,
) -> ApplyResult:
    """Save the selected subset (``None`` ⇒ all currently-selected entries). A moved head
    ⇒ ``Conflict('head_moved')`` (API maps to ``409 SaveConflict``); nothing applied. A
    partial save rebuilds an open change set for the remainder. Re-applying is idempotent."""
    cs = await get_change_set(db, ctx, project_id=project_id, cs_id=cs_id)
    project = await db.get(Project, (ctx.tenant_id, project_id))
    if project is None:
        raise NotFound("project not found")
    if cs.state == "applied":
        return ApplyResult(project=project, change_set=cs, new_open_change_set_id=None)
    if cs.state != "open":
        raise Conflict("change set is not open")

    wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, cs.working_copy_id))
    if wc is None:
        raise NotFound("working copy not found")

    entries = (
        (
            await db.execute(
                select(ProjectChangeSetEntry).where(
                    ProjectChangeSetEntry.tenant_id == ctx.tenant_id,
                    ProjectChangeSetEntry.change_set_id == cs.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if selected_entry_ids is not None:
        wanted = set(selected_entry_ids)
        paths = [e.path for e in entries if e.id in wanted]
    else:
        paths = [e.path for e in entries if e.selected]
    if not paths:
        raise Invalid("no selected changes to save")

    try:
        result = await wc_svc.save(
            db, ctx, wc, selected_paths=paths, checkpoint=checkpoint_name is not None
        )
    except Conflict as exc:
        if exc.message == "head_moved":
            cs.state = "conflicted"
            await db.flush()
        raise
    if checkpoint_name:
        result.snapshot.reason = "checkpoint"

    cs.state = "applied"
    cs.created_snapshot_id = result.snapshot.id
    await db.flush()

    new_open_id: uuid.UUID | None = None
    if wc.state in ("open", "ready_for_review"):
        new_cs = await build_change_set(db, ctx, wc)
        new_open_id = new_cs.id if new_cs is not None else None

    project = await db.get(Project, (ctx.tenant_id, project_id))
    assert project is not None
    return ApplyResult(project=project, change_set=cs, new_open_change_set_id=new_open_id)


async def discard_change_set(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, cs_id: uuid.UUID
) -> ProjectWorkingCopy:
    cs = await get_change_set(db, ctx, project_id=project_id, cs_id=cs_id)
    wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, cs.working_copy_id))
    if wc is None:
        raise NotFound("working copy not found")
    await wc_svc.discard(db, ctx, wc)
    cs.state = "discarded"
    await db.flush()
    return wc


async def discard_working_copy(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, wc_id: uuid.UUID
) -> ProjectWorkingCopy:
    wc = await wc_svc.get_by_id(db, ctx, project_id=project_id, wc_id=wc_id)
    await wc_svc.discard(db, ctx, wc)
    for cs in (
        (
            await db.execute(
                select(ProjectChangeSet).where(
                    ProjectChangeSet.tenant_id == ctx.tenant_id,
                    ProjectChangeSet.working_copy_id == wc.id,
                    ProjectChangeSet.state == "open",
                )
            )
        )
        .scalars()
        .all()
    ):
        cs.state = "discarded"
    await db.flush()
    return wc


# --- artifacts --------------------------------------------------------------


async def record_artifact(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    run_id: uuid.UUID | None,
    name: str,
    data: bytes,
    kind: str = "log",
    mime: str | None = "text/plain",
) -> ProjectArtifact:
    """Stage an EPHEMERAL run output (charges no quota until Keep/Export). The blob is
    written (ref_count 0 ⇒ uncounted) and swept by the Drive GC if never retained."""
    h, _ = await drive_svc.ensure_blob(
        db, ctx, wc.user_id, data=data, content_type=mime or "application/octet-stream"
    )
    art = ProjectArtifact(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=wc.project_id,
        working_copy_id=wc.id,
        run_id=run_id,
        user_id=wc.user_id,
        name=name[:200],
        kind=kind,
        content_hash=h,
        size_bytes=len(data),
        mime=mime,
        retention="ephemeral",
    )
    db.add(art)
    await db.flush()
    return art


async def list_artifacts(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    project_id: uuid.UUID,
    working_copy_id: uuid.UUID | None = None,
) -> list[ProjectArtifact]:
    stmt = select(ProjectArtifact).where(
        ProjectArtifact.tenant_id == ctx.tenant_id,
        ProjectArtifact.project_id == project_id,
        ProjectArtifact.user_id == ctx.user_id,
        ProjectArtifact.retention != "expired",
    )
    if working_copy_id is not None:
        stmt = stmt.where(ProjectArtifact.working_copy_id == working_copy_id)
    stmt = stmt.order_by(ProjectArtifact.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def _get_artifact(
    db: AsyncSession, ctx: CallerContext, project_id: uuid.UUID, artifact_id: uuid.UUID
) -> ProjectArtifact:
    art = await db.get(ProjectArtifact, (ctx.tenant_id, artifact_id))
    if art is None or art.project_id != project_id or art.user_id != ctx.user_id:
        raise NotFound("artifact not found")
    return art


async def keep_artifact(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, artifact_id: uuid.UUID
) -> ProjectArtifact:
    """Retain an artifact — now it charges quota (``507`` over quota). Idempotent."""
    art = await _get_artifact(db, ctx, project_id, artifact_id)
    if art.retention == "retained":
        return art
    if art.content_hash is None:
        raise Invalid("artifact has no content")
    acct = await drive_svc.get_account(db, ctx, art.user_id)
    blob = await db.get(StorageBlob, (ctx.tenant_id, art.user_id, art.content_hash))
    incoming = 0 if (blob is not None and blob.ref_count > 0) else art.size_bytes
    if acct.used_bytes + acct.reserved_bytes + incoming > acct.quota_bytes:
        raise InsufficientStorage("quota exceeded")
    art.retention = "retained"
    art.retained_at = _now()
    await db.flush()
    await drive_svc.recompute_blob(db, ctx, art.user_id, art.content_hash)
    await drive_svc.recompute_used(db, ctx, art.user_id)
    return art


async def export_artifact(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    drive_folder_id: uuid.UUID | None = None,
    name: str | None = None,
) -> DriveNode:
    """Copy an artifact's bytes into Drive (charges Drive quota via the new node)."""
    art = await _get_artifact(db, ctx, project_id, artifact_id)
    if art.content_hash is None:
        raise Invalid("artifact has no content")
    data = await _read_blob(db, ctx, art.user_id, art.content_hash)
    return await drive_svc.upload(
        db,
        ctx,
        parent_id=drive_folder_id,
        name=(name or art.name),
        data=data,
        content_type=art.mime or "application/octet-stream",
    )
