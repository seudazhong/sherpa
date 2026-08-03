"""Workspace Projects W3 task-working-copy lifecycle (ADR-040 + ADR-039).

A Project-bound Chat's first mutating action lazily opens a **durable task working copy**
from the current Project head (``base_snapshot_id`` + ``base_head_generation``). The working
copy is the authoritative pending state that spans chat turns; the materialized scratch tree
/ warm container (W3.2) are rebuildable caches of it.

This module owns the durable, docker-free half of W3:

* **lazy open** — at most one live working copy per Project-bound Chat (uq index);
  isolated per session.
* **single-writer lease + fence** — ``fence_token`` (bumped on lease (re)acquire) is
  stamped on every overlay publish; a stale sandbox (older fence) can never publish.
* **persist boundary** — after a bounded batch the scratch delta is persisted into the
  overlay (``project_working_copy_entries``); content-addressed blobs dedupe so the write
  is idempotent. New pending durable bytes are **reserved** against the shared ADR-030
  storage account (``507`` over quota); the reservation is released on save/discard/expire.
* **Save CAS** — Save selected / Save + checkpoint build a new immutable snapshot and
  advance ``projects.current_snapshot_id`` **and** ``head_generation`` in one transaction,
  gated by a compare-and-set on ``(current_snapshot_id, head_generation)``. A moved head
  ⇒ ``conflicted`` (``409 head_moved``), nothing applied.
* **Discard / idle expiry** — release the reservation, leave the head byte-identical to the
  base; idle-expiry release and reservation release are one atomic transition.

The sandbox orchestration (materialize scratch, run the hardened container, compute the
delta) is W3.2; the reviewable change-set projection + diffs are W3.3. The caller owns the
transaction (services flush, never commit).
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Project,
    ProjectSnapshot,
    ProjectSnapshotEntry,
    ProjectWorkingCopy,
    ProjectWorkingCopyEntry,
    StorageBlob,
)
from app.models import Session as SessionModel
from app.services import drive as drive_svc
from app.services import projects as projects_svc
from app.services.archive import ArchiveError, _normalize_path
from app.services.context import CallerContext
from app.services.errors import Conflict, InsufficientStorage, Invalid, NotFound

_LIVE_STATES = ("open", "ready_for_review")
_MAX_PATH_DEPTH = 64
_MAX_PATH_LENGTH = 1024


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# --- specs ------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class OverlayDelta:
    """One file change to publish into the durable overlay. Blobs for added/modified
    files MUST already exist in ``storage_blobs`` (the sandbox stages them, W3.2)."""

    path: str
    change_kind: str  # added | modified | deleted
    entry_kind: str = "file"  # file | dir | symlink
    content_hash: bytes | None = None
    size_bytes: int = 0
    executable: bool = False
    symlink_target: str | None = None


@dataclasses.dataclass(frozen=True)
class EffectiveEntry:
    path: str
    entry_kind: str
    content_hash: bytes | None
    size_bytes: int
    executable: bool
    symlink_target: str | None


# --- lazy open / lookup -----------------------------------------------------


async def get_live(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> ProjectWorkingCopy | None:
    """The live (open/ready_for_review) working copy for a session, or None."""
    return await db.scalar(
        select(ProjectWorkingCopy).where(
            ProjectWorkingCopy.tenant_id == ctx.tenant_id,
            ProjectWorkingCopy.session_id == session_id,
            ProjectWorkingCopy.user_id == ctx.user_id,
            ProjectWorkingCopy.state.in_(_LIVE_STATES),
        )
    )


async def get_by_id(
    db: AsyncSession, ctx: CallerContext, *, project_id: uuid.UUID, wc_id: uuid.UUID
) -> ProjectWorkingCopy:
    wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, wc_id))
    if wc is None or wc.user_id != ctx.user_id or wc.project_id != project_id:
        raise NotFound("working copy not found")
    return wc


async def open_working_copy(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> ProjectWorkingCopy:
    """Lazily open (or return the existing live) durable working copy for a Project-bound
    Chat. The session must be bound to a Project (``sessions.project_id``) that has a head
    snapshot. Idempotent: a second call returns the same live working copy."""
    uid = projects_svc._require_user(ctx)
    # Serialize lazy-open on the durable session row. A "SELECT live row, then INSERT"
    # sequence cannot lock a row that does not exist and races on uq_pwc_live_session.
    session = await db.scalar(
        select(SessionModel)
        .where(
            SessionModel.tenant_id == ctx.tenant_id,
            SessionModel.id == session_id,
        )
        .with_for_update()
    )
    if session is None or session.user_id != uid or session.status == "deleted":
        raise NotFound("session not found")
    existing = await get_live(db, ctx, session_id=session_id)
    if existing is not None:
        return existing
    if session.project_id is None:
        raise Invalid("session is not bound to a project (General chat has no working copy)")
    project = await db.get(Project, (ctx.tenant_id, session.project_id))
    if project is None or project.status == "deleting":
        raise NotFound("project not found")
    if project.current_snapshot_id is None:
        raise Invalid("project has no head snapshot")

    wc = ProjectWorkingCopy(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=project.id,
        session_id=session_id,
        user_id=uid,
        base_snapshot_id=project.current_snapshot_id,
        base_head_generation=project.head_generation,
        state="open",
        fence_token=0,
        reserved_bytes=0,
        expires_at=_now() + datetime.timedelta(seconds=settings.working_copy_idle_ttl_seconds),
    )
    db.add(wc)
    await db.flush()
    return wc


# --- single-writer lease + fence --------------------------------------------


async def acquire_lease(
    db: AsyncSession,
    wc: ProjectWorkingCopy,
    *,
    owner: str,
    ttl_seconds: int | None = None,
) -> int:
    """Acquire (or renew) the single-writer lease, bumping ``fence_token``. The returned
    fence must be stamped on every subsequent overlay publish for this batch; a later
    lease acquisition bumps the fence and invalidates an in-flight stale writer."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.sandbox_run_timeout_seconds * 4
    wc.fence_token += 1
    wc.lease_owner = owner
    wc.lease_expires_at = _now() + datetime.timedelta(seconds=ttl)
    await db.flush()
    return wc.fence_token


# --- overlay compose / rollups / reservation --------------------------------


async def _overlay_entries(
    db: AsyncSession, tenant_id: uuid.UUID, wc_id: uuid.UUID
) -> list[ProjectWorkingCopyEntry]:
    rows = (
        (
            await db.execute(
                select(ProjectWorkingCopyEntry)
                .where(
                    ProjectWorkingCopyEntry.tenant_id == tenant_id,
                    ProjectWorkingCopyEntry.working_copy_id == wc_id,
                )
                .order_by(ProjectWorkingCopyEntry.path)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _base_entries(
    db: AsyncSession, tenant_id: uuid.UUID, snapshot_id: uuid.UUID
) -> dict[str, ProjectSnapshotEntry]:
    rows = (
        (
            await db.execute(
                select(ProjectSnapshotEntry).where(
                    ProjectSnapshotEntry.tenant_id == tenant_id,
                    ProjectSnapshotEntry.snapshot_id == snapshot_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {r.path: r for r in rows}


async def effective_tree(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> dict[str, EffectiveEntry]:
    """The composed effective tree = base snapshot with the whole overlay applied
    (added/modified replace, deleted whiteout). Used to materialize scratch (W3.2) and to
    build the change set / save snapshot (W3.3)."""
    base = await _base_entries(db, ctx.tenant_id, wc.base_snapshot_id)
    result: dict[str, EffectiveEntry] = {
        p: EffectiveEntry(
            path=e.path,
            entry_kind=e.entry_kind,
            content_hash=e.content_hash,
            size_bytes=e.size_bytes,
            executable=e.executable,
            symlink_target=e.symlink_target,
        )
        for p, e in base.items()
    }
    for o in await _overlay_entries(db, ctx.tenant_id, wc.id):
        if o.change_kind == "deleted":
            result.pop(o.path, None)
        else:
            result[o.path] = EffectiveEntry(
                path=o.path,
                entry_kind=o.entry_kind,
                content_hash=o.content_hash,
                size_bytes=o.size_bytes,
                executable=o.executable,
                symlink_target=o.symlink_target,
            )
    return result


async def _sync_rollups_and_reservation(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> None:
    """Recompute overlay rollups + the quota reservation for pending NEW durable bytes.

    A blob referenced only by the overlay has ``ref_count == 0`` (it is not yet in a saved
    snapshot), so it is uncounted in the account's ``used_bytes`` and must be reserved.
    Reservation delta is mirrored into the shared storage account; ``507`` if it would
    exceed quota (checked BEFORE the reservation is enlarged)."""
    entries = await _overlay_entries(db, ctx.tenant_id, wc.id)
    distinct: dict[bytes, int] = {}
    for e in entries:
        if e.change_kind in ("added", "modified") and e.content_hash is not None:
            distinct[e.content_hash] = e.size_bytes

    overlay_bytes = 0
    want_reserved = 0
    for h, size in distinct.items():
        overlay_bytes += size
        blob = await db.get(StorageBlob, (ctx.tenant_id, wc.user_id, h))
        if blob is None or blob.ref_count == 0:
            want_reserved += size

    acct = await drive_svc.get_account(db, ctx, wc.user_id)
    delta = want_reserved - wc.reserved_bytes
    if delta > 0 and acct.used_bytes + acct.reserved_bytes + delta > acct.quota_bytes:
        raise InsufficientStorage("quota exceeded")
    acct.reserved_bytes = max(acct.reserved_bytes + delta, 0)
    wc.reserved_bytes = want_reserved
    wc.overlay_bytes = overlay_bytes
    wc.overlay_entry_count = len(entries)
    await db.flush()


async def _release_reservation(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> None:
    if wc.reserved_bytes:
        acct = await drive_svc.get_account(db, ctx, wc.user_id)
        acct.reserved_bytes = max(acct.reserved_bytes - wc.reserved_bytes, 0)
    wc.reserved_bytes = 0


async def persist_overlay(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    fence_token: int,
    deltas: list[OverlayDelta],
    run_id: uuid.UUID | None = None,
) -> bool:
    """Fence-guarded, idempotent overlay persist at an execution boundary. Returns False
    (and mutates nothing) if the caller holds a **stale** fence — a sandbox whose fence is
    behind the working copy's current ``fence_token`` can never publish (events §2.11 ②).
    Raises InsufficientStorage if reserving the new durable bytes would exceed quota."""
    if wc.state not in _LIVE_STATES:
        raise Conflict("working copy is not open")
    if fence_token < wc.fence_token:
        return False  # stale fence — cannot publish

    base = await _base_entries(db, ctx.tenant_id, wc.base_snapshot_id)
    for d in deltas:
        try:
            path = _normalize_path(d.path, depth_cap=_MAX_PATH_DEPTH, length_cap=_MAX_PATH_LENGTH)
        except ArchiveError as exc:
            raise Invalid(f"unsafe overlay path: {d.path}") from exc
        if d.change_kind not in ("added", "modified", "deleted"):
            raise Invalid(f"bad change_kind: {d.change_kind}")
        existing = await db.scalar(
            select(ProjectWorkingCopyEntry).where(
                ProjectWorkingCopyEntry.tenant_id == ctx.tenant_id,
                ProjectWorkingCopyEntry.working_copy_id == wc.id,
                ProjectWorkingCopyEntry.path == path,
            )
        )
        if existing is not None:
            await db.delete(existing)
            await db.flush()

        base_entry = base.get(path)
        if d.change_kind == "deleted":
            # Deleting a path created only in the overlay reverts it to absence; a whiteout
            # is required only when the saved base actually contains the path.
            if base_entry is None:
                continue
            canonical_kind = "deleted"
        else:
            if d.content_hash is None:
                raise Invalid(f"content_hash required for {d.change_kind}: {path}")
            if (
                base_entry is not None
                and base_entry.entry_kind == d.entry_kind
                and base_entry.content_hash == d.content_hash
                and base_entry.executable == d.executable
                and base_entry.symlink_target == d.symlink_target
            ):
                # The mutation restored the saved bytes exactly. Removing the old overlay
                # entry is the canonical representation; do not leave a fake "modified".
                continue
            canonical_kind = "modified" if base_entry is not None else "added"

        db.add(
            ProjectWorkingCopyEntry(
                tenant_id=ctx.tenant_id,
                id=uuid.uuid4(),
                working_copy_id=wc.id,
                user_id=wc.user_id,
                path=path,
                change_kind=canonical_kind,
                entry_kind=d.entry_kind,
                content_hash=d.content_hash,
                size_bytes=d.size_bytes,
                executable=d.executable,
                symlink_target=d.symlink_target,
                fence_token=fence_token,
            )
        )
    await db.flush()
    await _sync_rollups_and_reservation(db, ctx, wc)

    wc.last_boundary_at = _now()
    if run_id is not None:
        wc.last_run_id = run_id
    wc.expires_at = _now() + datetime.timedelta(seconds=settings.working_copy_idle_ttl_seconds)
    wc.state = "ready_for_review" if wc.overlay_entry_count > 0 else "open"
    await db.flush()
    return True


# --- Save (compare-and-set head advance) ------------------------------------


def _dir_parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts) - 1)]


@dataclasses.dataclass(frozen=True)
class SaveResult:
    snapshot: ProjectSnapshot
    working_copy_state: str
    applied_paths: list[str]


async def _build_snapshot(
    db: AsyncSession,
    ctx: CallerContext,
    project: Project,
    entries: dict[str, EffectiveEntry],
    *,
    reason: str,
    pinned: bool,
) -> ProjectSnapshot:
    """Create a new immutable snapshot from an already-materialized effective tree (blobs
    already exist), synthesize parent dirs, then advance the head + recompute rollups."""
    snapshot = ProjectSnapshot(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=project.id,
        parent_id=project.current_snapshot_id,
        reason=reason,
        entry_count=0,
        size_bytes=0,
        pinned=pinned,
    )
    db.add(snapshot)
    await db.flush()

    dir_paths: set[str] = set()
    for path, e in entries.items():
        if e.entry_kind == "dir":
            dir_paths.add(path)
        for parent in _dir_parents(path):
            dir_paths.add(parent)

    entry_count = 0
    distinct_sizes: dict[bytes, int] = {}
    touched_hashes: set[bytes] = set()
    for dpath in sorted(dir_paths):
        db.add(
            ProjectSnapshotEntry(
                tenant_id=ctx.tenant_id,
                id=uuid.uuid4(),
                snapshot_id=snapshot.id,
                user_id=project.user_id,
                path=dpath,
                entry_kind="dir",
            )
        )
        entry_count += 1
    for path, e in entries.items():
        if e.entry_kind == "dir":
            continue
        if e.entry_kind == "file" and e.content_hash is not None:
            distinct_sizes[e.content_hash] = e.size_bytes
            touched_hashes.add(e.content_hash)
            db.add(
                ProjectSnapshotEntry(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    snapshot_id=snapshot.id,
                    user_id=project.user_id,
                    path=path,
                    entry_kind="file",
                    content_hash=e.content_hash,
                    size_bytes=e.size_bytes,
                    executable=e.executable,
                )
            )
        else:  # symlink
            db.add(
                ProjectSnapshotEntry(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    snapshot_id=snapshot.id,
                    user_id=project.user_id,
                    path=path,
                    entry_kind="symlink",
                    symlink_target=e.symlink_target,
                )
            )
        entry_count += 1

    snapshot.entry_count = entry_count
    snapshot.size_bytes = sum(distinct_sizes.values())
    await db.flush()

    for h in touched_hashes:
        await drive_svc.recompute_blob(db, ctx, project.user_id, h)
    await drive_svc.recompute_used(db, ctx, project.user_id)

    project.current_snapshot_id = snapshot.id
    project.head_generation += 1  # CAS token advances with the head
    project.last_activity_at = _now()
    await projects_svc._recompute_project_used(db, project)
    await db.flush()
    return snapshot


async def save(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    selected_paths: list[str] | None = None,
    checkpoint: bool = False,
) -> SaveResult:
    """Save selected / Save + checkpoint: build a new immutable snapshot applying the
    selected overlay subset (``None`` ⇒ all overlay entries), advancing the head under a
    **compare-and-set** on ``(current_snapshot_id, head_generation)``. A moved head ⇒
    ``conflicted`` + ``409 head_moved`` (nothing applied). Unselected entries stay in the
    working copy (rebased onto the new head). This is a human review gate — never an agent
    auto-apply."""
    if wc.state not in _LIVE_STATES:
        raise Conflict("working copy is not open")
    project = await db.get(Project, (ctx.tenant_id, wc.project_id))
    if project is None:
        raise NotFound("project not found")

    # Head-generation compare-and-set: the head must not have moved since open.
    if (
        project.current_snapshot_id != wc.base_snapshot_id
        or project.head_generation != wc.base_head_generation
    ):
        wc.state = "conflicted"
        await db.flush()
        raise Conflict("head_moved")

    overlay = await _overlay_entries(db, ctx.tenant_id, wc.id)
    if not overlay:
        raise Invalid("no changes to save")
    sel: set[str] | None = (
        None if selected_paths is None else {p.strip().strip("/") for p in selected_paths}
    )
    applied = [o for o in overlay if sel is None or o.path in sel]
    if not applied:
        raise Invalid("no selected changes to save")

    # Effective tree for the save = base + the SELECTED overlay entries applied.
    base = await _base_entries(db, ctx.tenant_id, wc.base_snapshot_id)
    effective: dict[str, EffectiveEntry] = {
        p: EffectiveEntry(
            path=e.path,
            entry_kind=e.entry_kind,
            content_hash=e.content_hash,
            size_bytes=e.size_bytes,
            executable=e.executable,
            symlink_target=e.symlink_target,
        )
        for p, e in base.items()
    }
    for o in applied:
        if o.change_kind == "deleted":
            effective.pop(o.path, None)
        else:
            effective[o.path] = EffectiveEntry(
                path=o.path,
                entry_kind=o.entry_kind,
                content_hash=o.content_hash,
                size_bytes=o.size_bytes,
                executable=o.executable,
                symlink_target=o.symlink_target,
            )

    reason = "checkpoint" if checkpoint else "save"
    snapshot = await _build_snapshot(db, ctx, project, effective, reason=reason, pinned=checkpoint)

    applied_paths = [o.path for o in applied]
    for o in applied:
        await db.delete(o)
    await db.flush()

    remaining = await _overlay_entries(db, ctx.tenant_id, wc.id)
    if remaining:
        # Rebase the still-pending overlay onto the new head (the applied subset is now in
        # the base; the untouched-by-save paths remain valid deltas vs the new head).
        wc.base_snapshot_id = snapshot.id
        wc.base_head_generation = project.head_generation
        wc.state = "ready_for_review"
        await _sync_rollups_and_reservation(db, ctx, wc)
    else:
        await _release_reservation(db, ctx, wc)
        wc.state = "saved"
        wc.overlay_entry_count = 0
        wc.overlay_bytes = 0
    await db.flush()
    return SaveResult(snapshot=snapshot, working_copy_state=wc.state, applied_paths=applied_paths)


# --- Discard / idle expiry --------------------------------------------------


async def discard(
    db: AsyncSession, ctx: CallerContext, wc: ProjectWorkingCopy
) -> ProjectWorkingCopy:
    """Delete the overlay/staged bytes, release the reservation, and leave the Project head
    byte-identical to the base snapshot (``state='discarded'``)."""
    if wc.state not in _LIVE_STATES:
        return wc
    for o in await _overlay_entries(db, ctx.tenant_id, wc.id):
        await db.delete(o)
    await _release_reservation(db, ctx, wc)
    wc.state = "discarded"
    wc.overlay_entry_count = 0
    wc.overlay_bytes = 0
    await db.flush()
    return wc


async def expire_idle(db: AsyncSession, *, limit: int = 100) -> int:
    """Sweep live working copies past their idle TTL → ``expired`` + release the
    reservation (one atomic transition per row). Returns the count expired. Caller commits.
    """
    now = _now()
    rows = (
        (
            await db.execute(
                select(ProjectWorkingCopy)
                .where(
                    ProjectWorkingCopy.state.in_(_LIVE_STATES),
                    ProjectWorkingCopy.expires_at.is_not(None),
                    ProjectWorkingCopy.expires_at < now,
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for wc in rows:
        ctx = CallerContext(tenant_id=wc.tenant_id, user_id=wc.user_id, actor="system")
        await _release_reservation(db, ctx, wc)
        wc.state = "expired"
        wc.overlay_entry_count = 0
        wc.overlay_bytes = 0
        n += 1
    await db.flush()
    return n


def head_moved(project: Project, wc: ProjectWorkingCopy) -> bool:
    """True when a Save would conflict (the Project head advanced since the wc's base)."""
    return (
        project.current_snapshot_id != wc.base_snapshot_id
        or project.head_generation != wc.base_head_generation
    )
