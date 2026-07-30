"""Workspace Projects W3 sandbox scratch mechanics (ADR-039 isolation).

The **one-time scratch copy** is the ONLY thing the sandbox ever touches read-write. This
module owns the host-side, docker-free mechanics (fully testable offline) plus the hardened
container runner:

* **materialize** ``base snapshot + persisted overlay`` (an effective tree of blob refs) into
  a **fresh, disposable, node-local** scratch dir under ``SANDBOX_SCRATCH_ROOT/<run>`` — ONLY
  project bytes are written; **never** a credential, the ``.env``, the docker socket, another
  Project, Drive, or the blob store itself. Every path is validated to resolve inside the
  scratch root (untrusted-input discipline; ADR-039 "the orchestrator validates the src path").
* **apply edits** host-side (write/delete a file in scratch), path-validated.
* **run a command** in the ADR-025 hardened, network-disabled container with ONLY the scratch
  bind-mounted read-write (``nosuid,nodev``); ``cap_drop=ALL``, non-root, read-only rootfs +
  tmpfs, mem/pids/cpu/wall caps, ``--rm``. Gated by ``SANDBOX_KIND`` (``disabled`` offline).
  **Every failure exit is one of the contract-named reasons** (``sandbox_disabled``,
  ``runtime_daemon_unreachable``, ``runtime_image_missing``, ``runtime_start_failed``,
  ``runtime_transport_failed``, ``error:<class>``; events §2.11 ④) — never a blanket collapse.
* **compute the delta** of scratch vs the materialized base (added/modified/deleted), bounded
  by ``WORKING_COPY_MAX_CHANGED_FILES``/``_BYTES``.
* **cleanup / orphan sweep** — scratch trees are rebuildable caches, never recovery truth.

``_execute_in_scratch`` is module-level so tests can substitute a fake (mirrors
``app.sandbox.runner``). Nothing here mutates the database; the durable overlay persist +
the ``project_runtime_sessions``/``project_exec_runs`` bookkeeping live in
:mod:`app.services.project_sandbox`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.config import settings
from app.sandbox.runner import (
    RUNTIME_DAEMON_UNREACHABLE,
    RUNTIME_IMAGE_MISSING,
    RUNTIME_START_FAILED,
    RUNTIME_TRANSPORT_FAILED,
    SANDBOX_DISABLED,
    RunResult,
    named_failure,
    unmodelled_failure,
)

BlobReader = Callable[[bytes], Awaitable[bytes]]


@dataclasses.dataclass(frozen=True)
class MaterializeEntry:
    """One effective-tree node to materialize (a blob ref, dir, or safe symlink)."""

    path: str
    entry_kind: str  # file | dir | symlink
    content_hash: bytes | None
    size_bytes: int
    executable: bool
    symlink_target: str | None


@dataclasses.dataclass(frozen=True)
class ScratchEdit:
    """A host-side scratch mutation applied before running a command."""

    path: str
    op: str  # write | delete
    data: bytes | None = None
    executable: bool = False


@dataclasses.dataclass(frozen=True)
class DeltaEntry:
    path: str
    change_kind: str  # added | modified | deleted
    data: bytes | None  # None for deleted
    size_bytes: int
    executable: bool


@dataclasses.dataclass(frozen=True)
class DeltaResult:
    entries: list[DeltaEntry]
    over_bounds: bool  # exceeded WORKING_COPY_MAX_CHANGED_FILES/_BYTES


class ScratchError(Exception):
    """A named, non-leaking scratch failure (path escape, unsafe symlink, too big)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- path safety ------------------------------------------------------------


def _scratch_root() -> Path:
    return Path(settings.sandbox_scratch_root).resolve()


def scratch_dir_for(run_id: str) -> Path:
    return _scratch_root() / run_id


def _safe_join(run_dir: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``run_dir`` or raise ``path_escape``. Rejects absolute paths,
    ``..`` traversal, and anything that resolves outside the scratch tree."""
    rel = rel.strip().strip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise ScratchError("path_escape")
    target = (run_dir / rel).resolve()
    root = run_dir.resolve()
    if root != target and root not in target.parents:
        raise ScratchError("path_escape")
    return target


# --- materialize ------------------------------------------------------------


async def materialize(
    run_id: str, entries: list[MaterializeEntry], read_blob: BlobReader
) -> dict[str, bytes]:
    """Write the effective tree into a FRESH scratch dir; return the base manifest
    ``{path: content_hash}`` for files (used to compute the delta). Only project bytes are
    written — never a credential. Raises ``ScratchError`` on a path escape / oversize tree."""
    run_dir = scratch_dir_for(run_id)
    await asyncio.to_thread(_reset_dir, run_dir)
    manifest: dict[str, bytes] = {}
    total = 0
    for e in sorted(entries, key=lambda x: x.path):
        if e.entry_kind == "dir":
            _safe_join(run_dir, e.path).mkdir(parents=True, exist_ok=True)
        elif e.entry_kind == "symlink":
            # Materialize as a regular file recording the target (no real symlink is
            # created in scratch — a symlink is never followed out of the tree).
            target = _safe_join(run_dir, e.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, e.symlink_target or "")
        else:  # file
            if e.content_hash is None:
                continue
            data = await read_blob(e.content_hash)
            total += len(data)
            if total > settings.sandbox_scratch_max_bytes:
                raise ScratchError("scratch_too_large")
            target = _safe_join(run_dir, e.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, data)
            if e.executable:
                await asyncio.to_thread(_chmod_exec, target)
            manifest[e.path] = e.content_hash
    return manifest


def _reset_dir(run_dir: Path) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)


def _chmod_exec(target: Path) -> None:
    mode = target.stat().st_mode
    target.chmod(mode | 0o111)


# --- host-side edits --------------------------------------------------------


def apply_edit(run_id: str, edit: ScratchEdit) -> None:
    run_dir = scratch_dir_for(run_id)
    target = _safe_join(run_dir, edit.path)
    if edit.op == "delete":
        if target.exists():
            target.unlink()
        return
    if edit.op == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(edit.data or b"")
        if edit.executable:
            _chmod_exec(target)
        return
    raise ScratchError("bad_edit_op")


# --- delta ------------------------------------------------------------------


def compute_delta(run_id: str, base_manifest: dict[str, bytes]) -> DeltaResult:
    """Diff the scratch tree against the materialized base manifest → added/modified/deleted,
    bounded by ``WORKING_COPY_MAX_CHANGED_FILES``/``_BYTES`` (over ⇒ ``over_bounds``)."""
    run_dir = scratch_dir_for(run_id)
    current: dict[str, Path] = {}
    for dirpath, _dirs, files in os.walk(run_dir):
        for fn in files:
            full = Path(dirpath) / fn
            if full.is_symlink():
                continue
            rel = full.relative_to(run_dir).as_posix()
            current[rel] = full

    entries: list[DeltaEntry] = []
    changed_bytes = 0
    over = False
    max_files = settings.working_copy_max_changed_files
    max_bytes = settings.working_copy_max_changed_bytes

    for rel, full in sorted(current.items()):
        data = full.read_bytes()
        digest = hashlib.sha256(data).digest()
        base = base_manifest.get(rel)
        if base is None:
            kind = "added"
        elif base != digest:
            kind = "modified"
        else:
            continue
        changed_bytes += len(data)
        if len(entries) >= max_files or changed_bytes > max_bytes:
            over = True
            break
        entries.append(
            DeltaEntry(
                path=rel,
                change_kind=kind,
                data=data,
                size_bytes=len(data),
                executable=bool(full.stat().st_mode & 0o111),
            )
        )

    if not over:
        for rel in sorted(base_manifest):
            if rel not in current:
                if len(entries) >= max_files:
                    over = True
                    break
                entries.append(
                    DeltaEntry(
                        path=rel, change_kind="deleted", data=None, size_bytes=0, executable=False
                    )
                )
    return DeltaResult(entries=entries, over_bounds=over)


# --- hardened container run (ADR-025 + one-time scratch RW mount, ADR-039) ---
#
# The named termination reasons + the ``named_failure``/``unmodelled_failure`` helpers are
# shared with :mod:`app.sandbox.runner` (imported above): one vocabulary for both sandbox
# entry points, so no caller can reinvent a blanket collapse.


def _run_docker(scratch_dir: str, command: str) -> RunResult:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound
    from docker.types import Mount

    try:
        client = docker.from_env()
    except DockerException as exc:
        return named_failure(RUNTIME_DAEMON_UNREACHABLE, exc)
    except Exception as exc:  # noqa: BLE001 - classify and observe, never crash the loop
        return unmodelled_failure(exc)

    try:
        container = client.containers.run(
            settings.sandbox_image,
            command=["/bin/sh", "-lc", command],
            # ADR-039: the ONLY read-write mount is the disposable scratch copy.
            mounts=[Mount(target="/work", source=scratch_dir, type="bind", read_only=False)],
            working_dir="/work",
            network_disabled=True,
            mem_limit=f"{settings.sandbox_mem_mb}m",
            pids_limit=settings.sandbox_pids_limit,
            nano_cpus=1_000_000_000,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            read_only=True,  # rootfs read-only; only /work + /tmp are writable
            tmpfs={"/tmp": "size=64m,mode=1777,nosuid,nodev"},
            user="nobody",
            detach=True,
        )
    except ImageNotFound as exc:
        # The offline sandbox never reaches the network to pull: a missing image is its own
        # named, actionable outcome — not a generic "unavailable".
        return named_failure(RUNTIME_IMAGE_MISSING, exc)
    except (APIError, DockerException) as exc:
        return named_failure(RUNTIME_START_FAILED, exc)
    except Exception as exc:  # noqa: BLE001
        return unmodelled_failure(exc)

    timed_out = False
    try:
        try:
            res = container.wait(timeout=settings.sandbox_run_timeout_seconds)
            exit_code = int(res.get("StatusCode", -1))
        except Exception:
            timed_out = True
            exit_code = -1
            try:
                container.kill()
            except Exception:
                pass
        try:
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            # The container ran but its output could not be retrieved: distinct from a start
            # failure, because work may already have landed in the scratch tree.
            return named_failure(RUNTIME_TRANSPORT_FAILED, exc)
        return RunResult(stdout, stderr, exit_code, timed_out)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


async def _execute_in_scratch(scratch_dir: str, command: str) -> RunResult:
    return await asyncio.to_thread(_run_docker, scratch_dir, command)


async def run_in_scratch(run_id: str, command: str) -> RunResult:
    """Run ``command`` in the hardened container against ONLY the run's scratch tree.
    ``SANDBOX_KIND != docker`` (default) reports a clear disabled result for offline dev/tests;
    the real docker path is exercised in the browser. Every failure exit is one of the
    contract-named reasons above — never a blanket collapse."""
    if settings.sandbox_kind != "docker":
        return RunResult("", "", -1, False, error=SANDBOX_DISABLED)
    scratch_dir = str(scratch_dir_for(run_id))
    return await _execute_in_scratch(scratch_dir, command)


# --- cleanup / orphan sweep -------------------------------------------------


def cleanup(run_id: str) -> None:
    run_dir = scratch_dir_for(run_id)
    shutil.rmtree(run_dir, ignore_errors=True)


def sweep_orphans(keep_run_ids: set[str] | None = None) -> int:
    """Remove scratch trees left by crashed runs (a startup sweep). Scratch is a rebuildable
    cache — deleting it never loses a persisted boundary. Returns the count removed."""
    root = _scratch_root()
    if not root.exists():
        return 0
    keep = keep_run_ids or set()
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
