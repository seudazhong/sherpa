"""Host-side Project filesystem capability over the durable effective tree.

The Project snapshot + working-copy overlay are authoritative. These operations never
need a container and never advance the Project head; mutations stage reviewable overlay
entries and rebuild the current change set.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Project,
    ProjectRuntimeSession,
    ProjectSnapshotEntry,
    ProjectWorkingCopy,
    StorageBlob,
)
from app.models import Session as SessionModel
from app.objectstore import build_object_store
from app.services import drive as drive_svc
from app.services import project_changes as changes_svc
from app.services import project_workcopy as wc_svc
from app.services.archive import ArchiveError, _normalize_path
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound, TooLarge

_MAX_PATH_DEPTH = 64
_MAX_PATH_LENGTH = 1024
_READ_MAX_BYTES = 1_000_000
_GREP_MAX_FILE_BYTES = 1_000_000
_GREP_MAX_SCAN_BYTES = 5_000_000
_WINDOWS_DEVICES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


@dataclasses.dataclass(frozen=True)
class TreePage:
    entries: list[wc_svc.EffectiveEntry]
    truncated: bool


@dataclasses.dataclass(frozen=True)
class ReadPage:
    path: str
    content_hash: str
    executable: bool
    start_line: int
    lines: list[str]
    total_lines: int
    truncated: bool


@dataclasses.dataclass(frozen=True)
class GrepMatch:
    path: str
    line: int
    text: str


@dataclasses.dataclass(frozen=True)
class GrepPage:
    matches: list[GrepMatch]
    truncated: bool
    skipped_binary: int
    skipped_large: int


@dataclasses.dataclass(frozen=True)
class MutationResult:
    path: str
    content_hash: str | None
    change_set_id: uuid.UUID | None
    change_kind: str


def _user_id(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("project files require a user")
    return ctx.user_id


def normalize_path(raw: str, *, allow_root: bool = False) -> str:
    value = (raw or "").strip().replace("\\", "/")
    if allow_root and value in ("", ".", "/"):
        return "."
    try:
        path = _normalize_path(value, depth_cap=_MAX_PATH_DEPTH, length_cap=_MAX_PATH_LENGTH)
    except ArchiveError as exc:
        raise Invalid("unsafe_path") from exc
    for segment in path.split("/"):
        device = segment.rstrip(" .").split(".", 1)[0].casefold()
        if device in _WINDOWS_DEVICES:
            raise Invalid("unsafe_path")
    return path


async def _bound_session(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> SessionModel:
    session = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if (
        session is None
        or session.user_id != _user_id(ctx)
        or session.status == "deleted"
        or session.project_id is None
    ):
        raise Invalid("session is not bound to a project")
    return session


async def _head_tree(
    db: AsyncSession, ctx: CallerContext, project_id: uuid.UUID
) -> dict[str, wc_svc.EffectiveEntry]:
    project = await db.get(Project, (ctx.tenant_id, project_id))
    if project is None or project.user_id != _user_id(ctx) or project.current_snapshot_id is None:
        raise NotFound("project not found")
    rows = (
        (
            await db.execute(
                select(ProjectSnapshotEntry).where(
                    ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
                    ProjectSnapshotEntry.snapshot_id == project.current_snapshot_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        row.path: wc_svc.EffectiveEntry(
            path=row.path,
            entry_kind=row.entry_kind,
            content_hash=row.content_hash,
            size_bytes=row.size_bytes,
            executable=row.executable,
            symlink_target=row.symlink_target,
        )
        for row in rows
    }


async def effective_tree(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> tuple[SessionModel, ProjectWorkingCopy | None, dict[str, wc_svc.EffectiveEntry]]:
    """Read the pending effective tree without opening a working copy for read-only use."""
    session = await _bound_session(db, ctx, session_id=session_id)
    wc = await wc_svc.get_live(db, ctx, session_id=session_id)
    if wc is None:
        assert session.project_id is not None
        return session, None, await _head_tree(db, ctx, session.project_id)
    return session, wc, await wc_svc.effective_tree(db, ctx, wc)


async def list_entries(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    path: str = ".",
    max_entries: int = 200,
) -> TreePage:
    if not 1 <= max_entries <= 500:
        raise Invalid("max_entries must be between 1 and 500")
    prefix = normalize_path(path, allow_root=True)
    _session, _wc, tree = await effective_tree(db, ctx, session_id=session_id)
    entries = [
        entry
        for entry in tree.values()
        if prefix == "." or entry.path == prefix or entry.path.startswith(f"{prefix}/")
    ]
    entries.sort(key=lambda entry: entry.path)
    truncated = len(entries) > max_entries
    return TreePage(entries=entries[:max_entries], truncated=truncated)


async def _read_bytes(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    user_id: uuid.UUID,
    entry: wc_svc.EffectiveEntry,
) -> bytes:
    if entry.entry_kind != "file" or entry.content_hash is None:
        raise Invalid("not a file")
    if entry.size_bytes > _READ_MAX_BYTES:
        raise TooLarge("file exceeds fs_read byte limit")
    blob = await db.get(StorageBlob, (ctx.tenant_id, user_id, entry.content_hash))
    if blob is None:
        raise NotFound("blob missing")
    return await build_object_store().get(blob.object_key)


async def read_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    path: str,
    start_line: int = 1,
    max_lines: int = 500,
) -> ReadPage:
    if start_line < 1:
        raise Invalid("start_line must be at least 1")
    if not 1 <= max_lines <= 2000:
        raise Invalid("max_lines must be between 1 and 2000")
    norm = normalize_path(path)
    _session, _wc, tree = await effective_tree(db, ctx, session_id=session_id)
    entry = tree.get(norm)
    if entry is None:
        raise NotFound("path not found")
    data = await _read_bytes(db, ctx, user_id=_user_id(ctx), entry=entry)
    assert entry.content_hash is not None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Invalid("binary file") from exc
    all_lines = text.splitlines()
    begin = start_line - 1
    selected = all_lines[begin : begin + max_lines]
    return ReadPage(
        path=entry.path,
        content_hash=entry.content_hash.hex(),
        executable=entry.executable,
        start_line=start_line,
        lines=selected,
        total_lines=len(all_lines),
        truncated=begin + len(selected) < len(all_lines),
    )


async def grep(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    pattern: str,
    path: str = ".",
    max_results: int = 100,
) -> GrepPage:
    if not pattern or len(pattern) > 1000:
        raise Invalid("pattern required")
    if not 1 <= max_results <= 500:
        raise Invalid("max_results must be between 1 and 500")
    prefix = normalize_path(path, allow_root=True)
    _session, _wc, tree = await effective_tree(db, ctx, session_id=session_id)
    matches: list[GrepMatch] = []
    scanned = 0
    skipped_binary = 0
    skipped_large = 0
    truncated = False
    for entry in sorted(tree.values(), key=lambda item: item.path):
        if entry.entry_kind != "file":
            continue
        if prefix != "." and entry.path != prefix and not entry.path.startswith(f"{prefix}/"):
            continue
        if entry.size_bytes > _GREP_MAX_FILE_BYTES:
            skipped_large += 1
            continue
        if scanned + entry.size_bytes > _GREP_MAX_SCAN_BYTES:
            truncated = True
            break
        scanned += entry.size_bytes
        try:
            data = await _read_bytes(db, ctx, user_id=_user_id(ctx), entry=entry)
            text = data.decode("utf-8")
        except (Invalid, UnicodeDecodeError):
            skipped_binary += 1
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern not in line:
                continue
            matches.append(GrepMatch(entry.path, line_number, line[:500]))
            if len(matches) >= max_results:
                truncated = True
                return GrepPage(matches, truncated, skipped_binary, skipped_large)
    return GrepPage(matches, truncated, skipped_binary, skipped_large)


async def _locked_working_copy(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> ProjectWorkingCopy:
    wc = await wc_svc.open_working_copy(db, ctx, session_id=session_id)
    locked = await db.scalar(
        select(ProjectWorkingCopy)
        .where(
            ProjectWorkingCopy.tenant_id == ctx.tenant_id,
            ProjectWorkingCopy.id == wc.id,
        )
        .with_for_update()
    )
    if locked is None:
        raise NotFound("working copy not found")
    runtime = await db.scalar(
        select(ProjectRuntimeSession)
        .where(
            ProjectRuntimeSession.tenant_id == ctx.tenant_id,
            ProjectRuntimeSession.working_copy_id == locked.id,
            ProjectRuntimeSession.state.in_(("opening", "ready", "executing", "closing")),
        )
        .with_for_update()
    )
    if runtime is not None and runtime.state == "executing":
        raise Conflict("runtime_busy")
    # P4.2 replaces this temporary invalidation marker with container teardown. Keeping
    # the state transition here makes the fs/runtime serialization rule testable now.
    if runtime is not None and runtime.state == "ready":
        runtime.state = "opening"
        runtime.container_ref = None
    return locked


def _guard_hash(entry: wc_svc.EffectiveEntry | None, if_hash: str | None) -> None:
    if if_hash is None:
        return
    actual = entry.content_hash.hex() if entry is not None and entry.content_hash else None
    if actual != if_hash:
        raise Conflict("content_hash_mismatch")


async def _persist(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    deltas: list[wc_svc.OverlayDelta],
    invocation_id: uuid.UUID | None,
) -> uuid.UUID | None:
    live_runtime = await db.scalar(
        select(ProjectRuntimeSession).where(
            ProjectRuntimeSession.tenant_id == ctx.tenant_id,
            ProjectRuntimeSession.working_copy_id == wc.id,
            ProjectRuntimeSession.state.in_(("opening", "ready", "closing")),
        )
    )
    if live_runtime is not None and live_runtime.fence_token is not None:
        fence = live_runtime.fence_token
    else:
        fence = await wc_svc.acquire_lease(db, wc, owner=f"fs:{invocation_id or uuid.uuid4()}")
    published = await wc_svc.persist_overlay(
        db,
        ctx,
        wc,
        fence_token=fence,
        deltas=deltas,
        run_id=ctx.run_id,
    )
    if not published:
        raise Conflict("fence_lost")
    change_set = await changes_svc.build_change_set(db, ctx, wc, run_id=ctx.run_id)
    return change_set.id if change_set is not None else None


async def write_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    path: str,
    content: str,
    executable: bool = False,
    if_hash: str | None = None,
) -> MutationResult:
    norm = normalize_path(path)
    wc = await _locked_working_copy(db, ctx, session_id=session_id)
    tree = await wc_svc.effective_tree(db, ctx, wc)
    current = tree.get(norm)
    _guard_hash(current, if_hash)
    if current is not None and current.entry_kind != "file":
        raise Invalid("path is not a regular file")
    data = content.encode("utf-8")
    if len(data) > _READ_MAX_BYTES:
        raise TooLarge("file exceeds fs_write byte limit")
    content_hash, _ = await drive_svc.ensure_blob(
        db, ctx, wc.user_id, data=data, content_type="text/plain; charset=utf-8"
    )
    change_set_id = await _persist(
        db,
        ctx,
        wc,
        deltas=[
            wc_svc.OverlayDelta(
                path=norm,
                change_kind="modified" if current is not None else "added",
                content_hash=content_hash,
                size_bytes=len(data),
                executable=executable,
            )
        ],
        invocation_id=ctx.invocation_id,
    )
    return MutationResult(
        norm,
        content_hash.hex(),
        change_set_id,
        "modified" if current is not None else "added",
    )


async def edit_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    path: str,
    old_text: str,
    new_text: str,
    expect_occurrences: int = 1,
) -> MutationResult:
    if not old_text or len(old_text) > 100_000 or len(new_text) > 100_000:
        raise Invalid("edit text exceeds bounds")
    if not 1 <= expect_occurrences <= 100:
        raise Invalid("expect_occurrences must be between 1 and 100")
    norm = normalize_path(path)
    wc = await _locked_working_copy(db, ctx, session_id=session_id)
    tree = await wc_svc.effective_tree(db, ctx, wc)
    current = tree.get(norm)
    if current is None:
        raise NotFound("path not found")
    data = await _read_bytes(db, ctx, user_id=wc.user_id, entry=current)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Invalid("binary file") from exc
    occurrences = text.count(old_text)
    if occurrences != expect_occurrences:
        raise Conflict(f"expected {expect_occurrences} occurrences, found {occurrences}")
    updated = text.replace(old_text, new_text, expect_occurrences)
    return await write_file(
        db,
        ctx,
        session_id=session_id,
        path=norm,
        content=updated,
        executable=current.executable,
        if_hash=current.content_hash.hex() if current.content_hash else None,
    )


async def delete_path(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    path: str,
    recursive: bool = False,
    if_hash: str | None = None,
) -> MutationResult:
    norm = normalize_path(path)
    wc = await _locked_working_copy(db, ctx, session_id=session_id)
    tree = await wc_svc.effective_tree(db, ctx, wc)
    current = tree.get(norm)
    if current is None:
        raise NotFound("path not found")
    _guard_hash(current, if_hash)
    targets = [
        entry for entry in tree.values() if entry.path == norm or entry.path.startswith(f"{norm}/")
    ]
    if current.entry_kind == "dir" and not recursive:
        raise Invalid("directory deletion requires recursive=true")
    if len(targets) > settings.working_copy_max_changed_files:
        raise TooLarge("delete exceeds working-copy changed-file limit")
    change_set_id = await _persist(
        db,
        ctx,
        wc,
        deltas=[
            wc_svc.OverlayDelta(
                path=entry.path,
                change_kind="deleted",
                entry_kind=entry.entry_kind,
            )
            for entry in targets
        ],
        invocation_id=ctx.invocation_id,
    )
    return MutationResult(norm, None, change_set_id, "deleted")
