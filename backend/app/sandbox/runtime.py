"""The single sandbox code path: named runtime exits + workspace mechanics + container run.

Phase TR P3 rewrote the transport (ADR-047). **There is no host scratch directory and no
bind mount any more.** The one-time disposable copy of the working copy lives in memory,
travels into the container as a tar (``put_archive`` into an **anonymous** ``/work``
volume) and comes back as a tar (``get_archive``). Nothing in this module ever passes a
filesystem path to the docker daemon, which is what made backlog B-8 structurally
unfixable on Windows + Docker Desktop: the old ``Mount(type="bind", source=...)`` resolved
the *worker container's* path against the *host* daemon.

This module owns:

* the **named runtime termination reasons** shared by every sandbox entry point (events
  §2.11 ④ / api §10.7). Every container-path failure gets its OWN name: the old blanket
  ``sandbox_unavailable`` made an unreachable daemon, a missing image and a failed create
  indistinguishable (backlog B-8). The named reason travels on ``RunResult.error``; the
  **raw** failure text travels separately on ``RunResult.error_detail`` and is for the
  operator log only — the model sees a static, redacted sentence
  (``runtime_failure_note``), never a host path or exception text (ADR-019).
* **materialize** ``base snapshot + persisted overlay`` (an effective tree of blob refs)
  into a fresh in-memory :class:`Workspace` — ONLY project bytes; **never** a credential,
  the ``.env``, the docker socket, another Project, Drive, or the blob store itself.
  Credential-shaped paths are held back from the sandbox boundary (see
  :mod:`app.sandbox.transport`) but stay in the working copy untouched.
* **apply edits** host-side against that in-memory copy, path-validated.
* **run a command** in the ADR-025 hardened, network-disabled container. Every hardening
  control is unchanged: ``network_disabled``, ``cap_drop=ALL``, ``no-new-privileges``,
  non-root, read-only rootfs + tmpfs ``/tmp``, mem/pids/cpu/wall caps, ``--rm``, and **no
  secret injection whatsoever**. Gated by ``SANDBOX_KIND`` (``disabled`` offline).
* **compute the delta** of the resulting tree vs the materialized base
  (added/modified/deleted), bounded by ``WORKING_COPY_MAX_CHANGED_FILES``/``_BYTES``.
* **orphan sweep** — containers labelled as ours, left by crashed runs. Containers are
  rebuildable caches, never recovery truth.

**Degradation guarantee (ADR-048 §决策2).** With ``SANDBOX_KIND=disabled`` the workspace,
the edits and the delta all still work; only *running* is lost, never *editing*.

``_execute_workspace`` is module-level so tests can substitute a fake. Nothing here mutates
the database; the durable overlay persist + the ``project_runtime_sessions``/
``project_exec_runs`` bookkeeping live in :mod:`app.services.project_sandbox`.

**Known ``nosuid,nodev`` caveat (honest record).** config §1.7 describes ``/work`` as an
anonymous volume with ``nosuid,nodev``. Docker's API exposes those flags for *tmpfs* mounts
and for bind mounts, but **not** for an anonymous volume declared by the image, so they are
NOT set today. The equivalent protection is carried by ``cap_drop=ALL`` +
``no-new-privileges`` + a non-root user (a setuid binary cannot gain privilege, and device
nodes cannot be created without ``CAP_MKNOD``). Recorded rather than papered over.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings
from app.sandbox.transport import (
    RUNNER_GID,
    RUNNER_UID,
    WORK_DIR,
    TarTransport,
    TransportError,
    WorkspaceFile,
    is_credential_path,
)

# --- named runtime termination reasons (events §2.11 ④) ---------------------

SANDBOX_DISABLED = "sandbox_disabled"
RUNTIME_DAEMON_UNREACHABLE = "runtime_daemon_unreachable"
RUNTIME_IMAGE_MISSING = "runtime_image_missing"
RUNTIME_START_FAILED = "runtime_start_failed"
RUNTIME_TRANSPORT_FAILED = "runtime_transport_failed"
MEM_LIMIT = "mem_limit"

#: One redacted, model-facing sentence per runtime reason. Static by construction: it names
#: the reason and stays actionable, but carries no host path, image reference, daemon message
#: or credential (ADR-019).
RUNTIME_FAILURE_NOTES: dict[str, str] = {
    SANDBOX_DISABLED: (
        "the sandbox runtime is disabled in this deployment, so no command was executed"
    ),
    RUNTIME_DAEMON_UNREACHABLE: (
        "the container runtime daemon could not be reached, so no command was executed"
    ),
    RUNTIME_IMAGE_MISSING: (
        "the sandbox runner image is not available locally, and the offline sandbox never "
        "reaches the network to pull it, so no command was executed"
    ),
    RUNTIME_START_FAILED: (
        "the sandbox container could not be created or started, so no command was executed"
    ),
    RUNTIME_TRANSPORT_FAILED: (
        "the sandbox workspace could not be transferred into or out of the container"
    ),
    MEM_LIMIT: "the command exceeded the sandbox memory limit and was killed",
}

UNMODELLED_NOTE = "the sandbox failed with an unmodelled internal error"

#: Operator-log detail is bounded; it never reaches the model.
DETAIL_MAX = 500

#: Captured stdout/stderr is bounded so a flooding command cannot exhaust worker memory.
#: The bounded text is marked truncated; the typed spill reference is api §7.2 debt (P2.8).
OUTPUT_MAX_BYTES = 1_000_000

#: Every container this deployment creates carries this label, so the startup sweep can find
#: orphans from a crashed worker without touching anybody else's containers.
RUNTIME_LABEL = "sherpa.runtime"


def runtime_failure_note(reason: str) -> str:
    """The redacted observation for a named runtime exit — safe to hand to the model."""
    return f"{reason}: {RUNTIME_FAILURE_NOTES.get(reason, UNMODELLED_NOTE)}"


@dataclasses.dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    error: str | None = None
    #: Bounded raw failure text for the OPERATOR log only. ``error`` carries the contract-named
    #: reason; this carries the underlying detail (image name, daemon message, host path) that
    #: must never be handed to the model (events §2.11 ④ + ADR-019).
    error_detail: str | None = None
    #: True when stdout/stderr hit ``OUTPUT_MAX_BYTES`` and were cut.
    output_truncated: bool = False
    #: Bytes pushed into the container by the tar ingress (recorded on the runtime session).
    ingress_bytes: int | None = None


def named_failure(reason: str, exc: BaseException) -> RunResult:
    return RunResult("", "", -1, False, error=reason, error_detail=str(exc)[:DETAIL_MAX])


def unmodelled_failure(exc: BaseException) -> RunResult:
    """``error:<class>`` — the contract's catch-all for a failure we did not model."""
    return named_failure(f"error:{type(exc).__name__}", exc)


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
    """A host-side mutation applied to the disposable copy before running a command."""

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
    """A named, non-leaking workspace failure (path escape, unsafe member, too big)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclasses.dataclass
class Workspace:
    """The one-time disposable copy of the working copy — in memory, never on the host disk.

    ``base_manifest`` is the content hash of every file as materialized, and is what the
    delta is computed against. ``held_back`` are the credential-shaped paths that never
    cross the sandbox boundary; they are merged back into the result tree before the delta
    so that holding a file back can never be mistaken for the sandbox *deleting* it.
    """

    files: dict[str, WorkspaceFile] = dataclasses.field(default_factory=dict)
    dirs: set[str] = dataclasses.field(default_factory=set)
    base_manifest: dict[str, bytes] = dataclasses.field(default_factory=dict)
    held_back: set[str] = dataclasses.field(default_factory=set)
    total_bytes: int = 0

    @property
    def sendable(self) -> dict[str, WorkspaceFile]:
        """The subset of the copy that may enter the tar."""
        return {p: f for p, f in self.files.items() if p not in self.held_back}


@dataclasses.dataclass(frozen=True)
class ExecOutcome:
    """The result of running one command plus, when it came back, the egress tree."""

    result: RunResult
    #: ``None`` when no tree returned (disabled sandbox, start failure, transport failure);
    #: the caller then falls back to the host-side copy so edits are never lost.
    files: dict[str, WorkspaceFile] | None = None


# --- path safety ------------------------------------------------------------


def _safe_path(rel: str) -> str:
    """Normalize a workspace-relative path or raise ``path_escape``. Rejects absolute
    paths, ``..`` traversal, NUL and empty results."""
    if "\x00" in rel:
        raise ScratchError("path_escape")
    candidate = rel.strip().replace("\\", "/")
    if candidate.startswith("/"):
        raise ScratchError("path_escape")
    parts: list[str] = []
    for seg in candidate.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ScratchError("path_escape")
        parts.append(seg)
    if not parts:
        raise ScratchError("path_escape")
    return "/".join(parts)


# --- materialize ------------------------------------------------------------


async def materialize(entries: list[MaterializeEntry], read_blob: BlobReader) -> Workspace:
    """Build a FRESH in-memory copy of the effective tree and record the base manifest.

    Only project bytes are read — never a credential. Credential-shaped paths are recorded
    in ``held_back`` (still present in ``files`` and ``base_manifest``, so the delta stays
    correct) but are never serialized into the tar. Raises ``ScratchError`` on a path escape
    or an oversize tree."""
    ws = Workspace()
    for e in sorted(entries, key=lambda x: x.path):
        path = _safe_path(e.path)
        if e.entry_kind == "dir":
            ws.dirs.add(path)
            continue
        if e.entry_kind == "symlink":
            # Materialized as a regular file recording the target: a symlink is never
            # followed out of the tree, and the container gets no real link to chase.
            data = (e.symlink_target or "").encode("utf-8")
        else:
            if e.content_hash is None:
                continue
            data = await read_blob(e.content_hash)
        ws.total_bytes += len(data)
        if ws.total_bytes > settings.sandbox_scratch_max_bytes:
            raise ScratchError("scratch_too_large")
        ws.files[path] = WorkspaceFile(data=data, executable=e.executable)
        if e.entry_kind == "file" and e.content_hash is not None:
            ws.base_manifest[path] = e.content_hash
        else:
            ws.base_manifest[path] = hashlib.sha256(data).digest()
        if is_credential_path(path):
            ws.held_back.add(path)
    return ws


# --- host-side edits --------------------------------------------------------


def apply_edit(ws: Workspace, edit: ScratchEdit) -> None:
    """Apply one host-side edit to the disposable copy (path-validated)."""
    path = _safe_path(edit.path)
    if edit.op == "delete":
        ws.files.pop(path, None)
        ws.held_back.discard(path)
        return
    if edit.op == "write":
        data = edit.data or b""
        ws.files[path] = WorkspaceFile(data=data, executable=edit.executable)
        if "/" in path:
            ws.dirs.add(path.rsplit("/", 1)[0])
        if is_credential_path(path):
            ws.held_back.add(path)
        return
    raise ScratchError("bad_edit_op")


# --- delta ------------------------------------------------------------------


def compute_delta(ws: Workspace, result_files: dict[str, WorkspaceFile]) -> DeltaResult:
    """Diff the resulting tree against the materialized base manifest →
    added/modified/deleted, bounded by ``WORKING_COPY_MAX_CHANGED_FILES``/``_BYTES``
    (over ⇒ ``over_bounds``).

    Held-back (credential-shaped) paths are merged back from the host-side copy first: the
    sandbox never saw them, so their absence from the egress tree is not a deletion."""
    current = dict(result_files)
    for path in ws.held_back:
        held = ws.files.get(path)
        if held is not None:
            current[path] = held
        else:
            current.pop(path, None)

    entries: list[DeltaEntry] = []
    changed_bytes = 0
    over = False
    max_files = settings.working_copy_max_changed_files
    max_bytes = settings.working_copy_max_changed_bytes

    for rel in sorted(current):
        f = current[rel]
        digest = hashlib.sha256(f.data).digest()
        base = ws.base_manifest.get(rel)
        if base is None:
            kind = "added"
        elif base != digest:
            kind = "modified"
        else:
            continue
        changed_bytes += len(f.data)
        if len(entries) >= max_files or changed_bytes > max_bytes:
            over = True
            break
        entries.append(
            DeltaEntry(
                path=rel,
                change_kind=kind,
                data=f.data,
                size_bytes=len(f.data),
                executable=f.executable,
            )
        )

    if not over:
        for rel in sorted(ws.base_manifest):
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


# --- hardened container run (ADR-025 hardening + ADR-047 tar transport) -----


def _transport() -> TarTransport:
    return TarTransport(max_bytes=settings.sandbox_scratch_max_bytes)


def _bounded_logs(container: Any, *, stdout: bool, stderr: bool) -> tuple[str, bool]:
    """Read a log stream with a hard byte cap: a flooding command must not exhaust the
    worker's memory. The typed spill reference for oversized output is api §7.2 debt."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in container.logs(stdout=stdout, stderr=stderr, stream=True):
        if total >= OUTPUT_MAX_BYTES:
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    raw = b"".join(chunks)
    if len(raw) > OUTPUT_MAX_BYTES:
        raw, truncated = raw[:OUTPUT_MAX_BYTES], True
    return raw.decode("utf-8", "replace"), truncated


def _transport_failure(exc: TransportError) -> RunResult:
    return RunResult("", "", -1, False, error=exc.code, error_detail=exc.detail[:DETAIL_MAX])


def _run_docker(ws: Workspace, command: str) -> ExecOutcome:
    """Create → tar in → start → wait → logs → tar out → remove. No mount, no host path."""
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound, NotFound

    try:
        client = docker.from_env()
    except DockerException as exc:
        return ExecOutcome(named_failure(RUNTIME_DAEMON_UNREACHABLE, exc))
    except Exception as exc:  # noqa: BLE001 - classify and observe, never crash the loop
        return ExecOutcome(unmodelled_failure(exc))

    try:
        container = client.containers.create(
            settings.sandbox_image,
            command=["/bin/sh", "-lc", command],
            # ADR-047: /work is the runner image's ANONYMOUS volume. There is no mounts=,
            # no binds= and no host path anywhere in this call — that is the whole point.
            working_dir=WORK_DIR,
            network_disabled=True,
            mem_limit=f"{settings.sandbox_mem_mb}m",
            pids_limit=settings.sandbox_pids_limit,
            nano_cpus=1_000_000_000,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            read_only=True,  # rootfs read-only; only /work + /tmp are writable
            tmpfs={"/tmp": "size=64m,mode=1777,nosuid,nodev"},
            user=f"{RUNNER_UID}:{RUNNER_GID}",
            labels={RUNTIME_LABEL: "1"},
        )
    except ImageNotFound as exc:
        # The offline sandbox never reaches the network to pull: a missing image is its own
        # named, actionable outcome — not a generic "unavailable".
        return ExecOutcome(named_failure(RUNTIME_IMAGE_MISSING, exc))
    except (APIError, DockerException) as exc:
        return ExecOutcome(named_failure(RUNTIME_START_FAILED, exc))
    except Exception as exc:  # noqa: BLE001
        return ExecOutcome(unmodelled_failure(exc))

    transport = _transport()
    try:
        try:
            ingress_bytes = transport.ingest(container, ws.sendable, ws.dirs)
        except TransportError as exc:
            return ExecOutcome(_transport_failure(exc))
        except (APIError, DockerException, OSError) as exc:
            return ExecOutcome(named_failure(RUNTIME_TRANSPORT_FAILED, exc))

        try:
            container.start()
        except (APIError, DockerException) as exc:
            return ExecOutcome(named_failure(RUNTIME_START_FAILED, exc))
        except Exception as exc:  # noqa: BLE001
            return ExecOutcome(unmodelled_failure(exc))

        timed_out = False
        try:
            res = container.wait(timeout=settings.sandbox_run_timeout_seconds)
            exit_code = int(res.get("StatusCode", -1))
        except Exception:  # noqa: BLE001 - any wait failure is a wall-clock kill for us
            timed_out = True
            exit_code = -1
            try:
                container.kill()
            except Exception:  # noqa: BLE001
                pass

        try:
            container.reload()
            oom = bool(container.attrs.get("State", {}).get("OOMKilled", False))
        except Exception:  # noqa: BLE001 - a missing OOM flag only costs us a better name
            oom = False

        try:
            stdout, t_out = _bounded_logs(container, stdout=True, stderr=False)
            stderr, t_err = _bounded_logs(container, stdout=False, stderr=True)
        except Exception as exc:  # noqa: BLE001
            # The container ran but its output could not be retrieved: distinct from a start
            # failure, because work may already have landed in the workspace.
            return ExecOutcome(named_failure(RUNTIME_TRANSPORT_FAILED, exc))
        truncated = t_out or t_err

        try:
            files = transport.egress(container)
        except TransportError as exc:
            return ExecOutcome(
                RunResult(
                    stdout,
                    stderr,
                    exit_code,
                    timed_out,
                    error=exc.code,
                    error_detail=exc.detail[:DETAIL_MAX],
                    output_truncated=truncated,
                    ingress_bytes=ingress_bytes,
                )
            )
        except NotFound as exc:
            return ExecOutcome(named_failure(RUNTIME_TRANSPORT_FAILED, exc))
        except (APIError, DockerException, OSError) as exc:
            return ExecOutcome(named_failure(RUNTIME_TRANSPORT_FAILED, exc))

        return ExecOutcome(
            RunResult(
                stdout,
                stderr,
                exit_code,
                timed_out,
                error=MEM_LIMIT if oom else None,
                output_truncated=truncated,
                ingress_bytes=ingress_bytes,
            ),
            files=files,
        )
    finally:
        try:
            # v=True also removes the anonymous /work volume: the disposable copy leaves
            # nothing behind on the node.
            container.remove(force=True, v=True)
        except Exception:  # noqa: BLE001
            pass


async def _execute_workspace(ws: Workspace, command: str) -> ExecOutcome:
    return await asyncio.to_thread(_run_docker, ws, command)


async def run_workspace(ws: Workspace, command: str) -> ExecOutcome:
    """Run ``command`` in the hardened container against ONLY this disposable copy.
    ``SANDBOX_KIND != docker`` (default) reports a clear disabled result for offline dev and
    tests — and the caller still persists the host-side edits, because losing *run* must
    never mean losing *edit* (ADR-048 §决策2). Every failure exit is one of the
    contract-named reasons above — never a blanket collapse."""
    if settings.sandbox_kind != "docker":
        return ExecOutcome(RunResult("", "", -1, False, error=SANDBOX_DISABLED))
    return await _execute_workspace(ws, command)


# --- orphan sweep -----------------------------------------------------------


def sweep_orphan_containers() -> int:
    """Remove sandbox containers left behind by a crashed worker. Containers are rebuildable
    caches — removing one never loses a persisted boundary. Only containers carrying this
    deployment's label are touched. Returns the count removed."""
    if settings.sandbox_kind != "docker":
        return 0
    try:
        import docker

        client = docker.from_env()
        containers = client.containers.list(all=True, filters={"label": RUNTIME_LABEL})
    except Exception:  # noqa: BLE001 - best-effort cache cleanup, never fatal to startup
        return 0
    removed = 0
    for c in containers:
        try:
            c.remove(force=True, v=True)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed
