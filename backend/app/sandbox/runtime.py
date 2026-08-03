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
import io
import json
import queue
import re
import tarfile
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings
from app.sandbox.transport import (
    RUNNER_GID,
    RUNNER_UID,
    WORK_DIR,
    ByteBuffer,
    TarTransport,
    TransportError,
    WorkspaceFile,
    is_credential_path,
)

# --- named runtime termination reasons (events §2.11 ④) ---------------------

SANDBOX_DISABLED = "sandbox_disabled"
RUNTIME_DAEMON_UNREACHABLE = "runtime_daemon_unreachable"
RUNTIME_IMAGE_MISSING = "runtime_image_missing"
RUNTIME_IMAGE_UNTRUSTED = "runtime_image_untrusted"
RUNTIME_START_FAILED = "runtime_start_failed"
RUNTIME_TRANSPORT_FAILED = "runtime_transport_failed"
MEM_LIMIT = "mem_limit"

#: An immutable image reference: a bare image ID digest (``sha256:<64 hex>``) or a
#: repository digest (``[host[:port]/]name@sha256:<64 hex>``). A tag is deliberately NOT
#: accepted — a tag can be re-pointed at different bytes after review, which is exactly what
#: config §1.7's "pinned digest" rule exists to prevent. The name part allows a registry
#: host with a port; the digest part stays strict (lowercase hex, exactly 64).
_IMAGE_DIGEST_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9._\-]*(?::[0-9]+)?(?:/[A-Za-z0-9._\-]+)*@)?sha256:[0-9a-f]{64}$"
)

#: The runner image advertises this OCI title.
#:
#: ⚠️ **This label is a compatibility guard, not a security control.** Labels are plain
#: image metadata: anyone who can build an image can set them, so the check cannot prove
#: provenance and is not claimed to. Its job is to catch *accidental misconfiguration* — a
#: digest pasted from the wrong image, which would otherwise start a container that has no
#: ``/work`` volume, no ``pytest``/``ruff``, and a root user, and fail in a confusing way
#: much later. **The real trust root is the operator-chosen digest in ``SANDBOX_IMAGE``**:
#: it is an allowlist of exactly one immutable image, and it is what makes "the bytes that
#: were reviewed are the bytes that run" true. Cryptographic provenance (signature or
#: attestation verification) is deliberately **out of scope** here — see config §1.7.
RUNNER_IMAGE_TITLE = "sherpa-sandbox-runner"
RUNNER_TITLE_LABEL = "org.opencontainers.image.title"
RUNNER_CAPABILITIES_LABEL = "sherpa.capabilities"

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
    RUNTIME_IMAGE_UNTRUSTED: (
        "the configured sandbox runner image is not an approved, digest-pinned first-party "
        "runner, so no command was executed"
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

#: Every container this deployment creates carries these labels.
#:
#: ``RUNTIME_LABEL`` identifies a Sherpa sandbox container in general. ``OWNER_LABEL`` is the
#: one that matters for cleanup: it names **which deployment** owns the container, and the
#: sweeper filters on it. Sweeping on ``RUNTIME_LABEL`` alone was a confirmed bug, not a
#: theoretical one — the dev worker's maintenance cron deleted containers belonging to a
#: concurrently running test lane, which surfaced as
#: ``409 container is dead or marked for removal``.
RUNTIME_LABEL = "sherpa.runtime"
OWNER_LABEL = "sherpa.owner"
#: The runtime session (or ``ephemeral``) a container was created for — operator-facing
#: identity when inspecting a leaked container.
SESSION_LABEL = "sherpa.runtime_session"
#: Unix seconds, by the creating process's clock, used by the age rule below.
STARTED_LABEL = "sherpa.started_at"

#: How long past the enforced wall clock a container may live before it is considered
#: orphaned. The orchestrator holds a container for at most
#: ``SANDBOX_RUN_TIMEOUT_SECONDS`` (enforced: `wait` kills it) plus the bounded post-exit
#: work — reading capped logs and pulling the capped egress tar. This grace covers that tail
#: generously, which is what makes "older than the threshold ⇒ nobody is still using it" a
#: safe inference rather than a hopeful one.
SWEEP_GRACE_SECONDS = 300

#: Container ids this process is actively using. A container in here is NEVER swept, whatever
#: its age or state: the sweeper and the run loop share a process, so this is exact for the
#: common case and the age rule below is the fail-safe for everything else.
_IN_FLIGHT: set[str] = set()


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
    #: ``None`` for a deletion. May be a ``bytearray`` whose ownership egress handed over
    #: (:data:`~app.sandbox.transport.ByteBuffer`) — **read-only**. The persist boundary
    #: converts to immutable ``bytes`` once, per file, because the content-addressed blob
    #: store requires it.
    data: ByteBuffer | None
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


@dataclasses.dataclass(frozen=True)
class BaselineEntry:
    """What a file looked like when the disposable copy was materialized.

    The delta compares **content hash and executable bit**. Hash alone was not enough: a
    `chmod +x` with byte-identical content produced an empty delta, so the mode change was
    silently dropped instead of reaching the change set."""

    content_hash: bytes
    executable: bool = False


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
    base_manifest: dict[str, BaselineEntry] = dataclasses.field(default_factory=dict)
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


@dataclasses.dataclass(frozen=True)
class RuntimeOpenOutcome:
    result: RunResult
    container_ref: str | None = None
    image_digest: str | None = None
    capabilities: dict[str, object] | None = None


@dataclasses.dataclass(frozen=True)
class RuntimeExecOutcome:
    result: RunResult
    files: dict[str, WorkspaceFile] | None = None
    container_alive: bool = False
    cancelled: bool = False


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
            digest = e.content_hash
        else:
            digest = hashlib.sha256(data).digest()
        ws.base_manifest[path] = BaselineEntry(content_hash=digest, executable=e.executable)
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
        elif base.content_hash != digest or base.executable != f.executable:
            # An executable-bit flip with byte-identical content IS a change: it is what
            # `chmod +x` does, it is persisted in the overlay, and comparing hashes alone
            # dropped it on the floor.
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


def _is_read_timeout(exc: BaseException) -> bool:
    """True only for a genuine *read* timeout on the wait call.

    This needs care, and getting it wrong is how a broken daemon gets reported to the user as
    "your command took too long". docker-py does **not** translate the timeout: measured
    against a real daemon, ``container.wait(timeout=N)`` raises
    ``requests.exceptions.ConnectionError(ReadTimeoutError(...))`` — the *same class* a
    genuinely unreachable daemon raises. Class alone therefore cannot decide it; the
    exception chain has to be walked for a timeout marker.

    **Connect timeouts are excluded, and that exclusion is load-bearing.** Both
    ``requests.ConnectTimeout`` (which subclasses ``Timeout``) and
    ``urllib3.ConnectTimeoutError`` (which subclasses urllib3's ``TimeoutError``) satisfy a
    naive "is this a timeout?" test, so an outage that stalled at connect time was being
    reported as the user's command running too long. A connect timeout means we never
    reached the daemon — and by the time ``wait`` is called this client has already created
    and started the container through the same connection pool, so the daemon *was*
    reachable a moment ago. Failing to reach it now is an outage, never a wall-clock
    expiry.
    """
    try:
        import requests.exceptions as rexc
        import urllib3.exceptions as uexc
    except Exception:  # noqa: BLE001 - if these are absent nothing here can be a timeout
        return False

    #: Checked FIRST: these are timeouts, but not *this* timeout.
    connect_types: tuple[type[BaseException], ...] = (
        rexc.ConnectTimeout,
        uexc.ConnectTimeoutError,
    )
    #: A read timeout on an established connection — the wall-clock expiry we want.
    read_types: tuple[type[BaseException], ...] = (
        rexc.ReadTimeout,
        uexc.ReadTimeoutError,
    )
    #: Generic timeouts, accepted only when nothing in the chain says "connect".
    generic_types: tuple[type[BaseException], ...] = (rexc.Timeout, TimeoutError)

    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        # requests wraps the urllib3 cause in .args rather than __cause__ in this path.
        nxt: BaseException | None = cur.__cause__ or cur.__context__
        if nxt is None:
            for arg in getattr(cur, "args", ()):
                if isinstance(arg, BaseException):
                    nxt = arg
                    break
        cur = nxt

    if any(isinstance(node, connect_types) for node in chain):
        return False
    if any(isinstance(node, read_types) for node in chain):
        return True
    return any(isinstance(node, generic_types) for node in chain)


def _classify_daemon_failure(exc: BaseException) -> RunResult:
    """Map a docker client failure onto its contract-named reason.

    The distinction that matters to an operator: an ``APIError`` means the daemon **answered**
    and answered with an error (a transport/API fault), while a connection failure means it
    could not be reached at all. Note ``requests.exceptions.ConnectionError`` is **not** a
    builtin ``ConnectionError`` — it inherits from ``OSError`` via ``RequestException`` — so
    it has to be named explicitly rather than caught by the builtin.
    """
    from docker.errors import APIError, DockerException

    if isinstance(exc, APIError):
        return named_failure(RUNTIME_TRANSPORT_FAILED, exc)
    try:
        import requests.exceptions as rexc

        if isinstance(exc, rexc.RequestException):
            return named_failure(RUNTIME_DAEMON_UNREACHABLE, exc)
    except Exception:  # noqa: BLE001 - requests missing just means we fall through
        pass
    try:
        import urllib3.exceptions as uexc

        # A bare urllib3 error can surface when docker-py does not wrap it. It is a
        # transport-layer failure to reach the daemon, not an unmodelled internal bug.
        if isinstance(exc, uexc.HTTPError):
            return named_failure(RUNTIME_DAEMON_UNREACHABLE, exc)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, DockerException | ConnectionError | OSError):
        return named_failure(RUNTIME_DAEMON_UNREACHABLE, exc)
    return unmodelled_failure(exc)


def _classify_wait_failure(exc: BaseException) -> RunResult:
    """A non-timeout ``container.wait`` failure. Same taxonomy as any other client call."""
    return _classify_daemon_failure(exc)


def _kill_quietly(container: Any) -> None:
    """Best-effort kill. The caller has already decided the named reason; failing to kill a
    container that may already be gone must not change it."""
    try:
        container.kill()
    except Exception:  # noqa: BLE001
        pass


def is_pinned_image_reference(ref: str) -> bool:
    """True when ``ref`` names bytes that cannot change under us (config §1.7)."""
    return bool(_IMAGE_DIGEST_RE.match(ref.strip()))


def verify_runner_image(client: Any, ref: str) -> RunResult | None:
    """Fail closed unless ``ref`` is a digest-pinned image that looks like our runner.

    Returns ``None`` when the image is accepted, or the named failure to report.

    **Threat boundary, stated honestly.** The security property comes from **one** thing:
    ``SANDBOX_IMAGE`` is an operator-chosen **immutable digest**, i.e. an allowlist of
    exactly one set of bytes. That is what makes "what was reviewed is what runs" true, and
    it is why a tag is refused outright — a tag can be re-pointed after review.

    The label check that follows is **not** a second security control. Labels are ordinary,
    forgeable image metadata; an attacker who can make the operator configure a hostile
    digest can equally set that digest's labels. It is a **compatibility / typo guard**:
    it turns "operator pasted the wrong digest" into one clear refusal instead of a
    container with no ``/work`` volume and no tooling failing confusingly later.

    No provenance or attestation is verified here, and none is claimed. Real supply-chain
    verification (cosign/in-toto style signature or attestation checking) is **out of
    scope** for v1 and is recorded as such in config §1.7 rather than implied by this
    check.
    """
    from docker.errors import ImageNotFound

    ref = (ref or "").strip()
    if not ref:
        return named_failure(
            RUNTIME_IMAGE_UNTRUSTED,
            ValueError(
                "SANDBOX_IMAGE is not set; build the runner "
                "(docker build -t sherpa-sandbox-runner:dev sandbox-runner) and pin it by "
                "digest (docker image inspect ... --format '{{.Id}}')"
            ),
        )
    if not is_pinned_image_reference(ref):
        return named_failure(
            RUNTIME_IMAGE_UNTRUSTED,
            ValueError(f"SANDBOX_IMAGE {ref!r} is not digest-pinned (expected sha256:<64 hex>)"),
        )
    try:
        image = client.images.get(ref)
    except ImageNotFound as exc:
        return named_failure(RUNTIME_IMAGE_MISSING, exc)
    except Exception as exc:  # noqa: BLE001 - classify, never crash the loop
        # An APIError means the daemon answered and answered with an error (transport/API
        # fault); a connection failure means we could not reach it at all. Collapsing both
        # into "unreachable" sent the operator to check the wrong thing.
        return _classify_daemon_failure(exc)

    labels = (image.labels or {}) if hasattr(image, "labels") else {}
    if labels.get(RUNNER_TITLE_LABEL) != RUNNER_IMAGE_TITLE:
        return named_failure(
            RUNTIME_IMAGE_UNTRUSTED,
            ValueError(
                f"image {ref!r} does not look like the Sherpa runner "
                f"({RUNNER_TITLE_LABEL}={labels.get(RUNNER_TITLE_LABEL)!r}); this is a "
                "misconfiguration guard, not a provenance check"
            ),
        )
    return None


def _create_hardened_container(client: Any, *, command: list[str], session_label: str) -> Any:
    """Create one runner container with the shared ADR-025 hardening profile."""
    return client.containers.create(
        settings.sandbox_image,
        command=command,
        # ADR-047: /work is the image's anonymous volume. No mount/bind/host path.
        working_dir=WORK_DIR,
        network_disabled=True,
        mem_limit=f"{settings.sandbox_mem_mb}m",
        pids_limit=settings.sandbox_pids_limit,
        nano_cpus=1_000_000_000,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        read_only=True,
        tmpfs={"/tmp": "size=64m,mode=1777,nosuid,nodev"},
        user=f"{RUNNER_UID}:{RUNNER_GID}",
        labels={
            RUNTIME_LABEL: "1",
            OWNER_LABEL: deployment_owner_id(),
            SESSION_LABEL: session_label,
            STARTED_LABEL: f"{time.time():.3f}",
        },
    )


def _run_docker(ws: Workspace, command: str, *, session_label: str = "ephemeral") -> ExecOutcome:
    """Create → tar in → start → wait → logs → tar out → remove. No mount, no host path."""
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound, NotFound

    try:
        client = docker.from_env()
    except DockerException as exc:
        return ExecOutcome(named_failure(RUNTIME_DAEMON_UNREACHABLE, exc))
    except Exception as exc:  # noqa: BLE001 - classify and observe, never crash the loop
        return ExecOutcome(unmodelled_failure(exc))

    # Fail closed BEFORE anything is created: an unpinned or foreign image never runs.
    rejected = verify_runner_image(client, settings.sandbox_image)
    if rejected is not None:
        return ExecOutcome(rejected)

    try:
        container = _create_hardened_container(
            client,
            command=["/bin/sh", "-lc", command],
            session_label=session_label,
        )
    except ImageNotFound as exc:
        # The offline sandbox never reaches the network to pull: a missing image is its own
        # named, actionable outcome — not a generic "unavailable".
        return ExecOutcome(named_failure(RUNTIME_IMAGE_MISSING, exc))
    except (APIError, DockerException) as exc:
        return ExecOutcome(named_failure(RUNTIME_START_FAILED, exc))
    except Exception as exc:  # noqa: BLE001
        return ExecOutcome(unmodelled_failure(exc))

    # From here until the `finally` this container is ours and in use: the sweeper must not
    # touch it even if a maintenance tick lands mid-run. This covers the `created` state
    # during tar ingress, which is where the observed 409s came from — a large workspace can
    # spend a long time uploading before `start()` is ever called.
    _IN_FLIGHT.add(container.id)

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
        except Exception as exc:  # noqa: BLE001 - classify, never crash the loop
            _kill_quietly(container)
            if not _is_read_timeout(exc):
                # A daemon that died, a dropped connection or an API error is NOT a wall
                # clock kill. Reporting it as `wall_timeout` told the user their command was
                # too slow when the truth was that the runtime broke underneath it.
                return ExecOutcome(_classify_wait_failure(exc))
            timed_out = True
            exit_code = -1
            _kill_quietly(container)

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
        # Released only after removal is attempted, so the sweeper can never see this
        # container as unowned while we are still touching it.
        _IN_FLIGHT.discard(container.id)


async def _execute_workspace(
    ws: Workspace, command: str, *, session_label: str = "ephemeral"
) -> ExecOutcome:
    return await asyncio.to_thread(_run_docker, ws, command, session_label=session_label)


async def run_workspace(
    ws: Workspace, command: str, *, session_label: str = "ephemeral"
) -> ExecOutcome:
    """Run ``command`` in the hardened container against ONLY this disposable copy.
    ``SANDBOX_KIND != docker`` (default) reports a clear disabled result for offline dev and
    tests — and the caller still persists the host-side edits, because losing *run* must
    never mean losing *edit* (ADR-048 §决策2). Every failure exit is one of the
    contract-named reasons above — never a blanket collapse."""
    if settings.sandbox_kind != "docker":
        return ExecOutcome(RunResult("", "", -1, False, error=SANDBOX_DISABLED))
    return await _execute_workspace(ws, command, session_label=session_label)


# --- explicit RuntimeSession container lifecycle (Phase TR P4) -------------


def _read_capabilities(container: Any) -> dict[str, object]:
    chunks, _stat = container.get_archive("/opt/sherpa/capabilities.json")
    raw = b"".join(chunks)
    if len(raw) > 128 * 1024:
        raise ValueError("capabilities manifest too large")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        member = next((item for item in archive.getmembers() if item.isfile()), None)
        if member is None or member.size > 64 * 1024:
            raise ValueError("capabilities manifest missing or too large")
        handle = archive.extractfile(member)
        if handle is None:
            raise ValueError("capabilities manifest unreadable")
        value = json.loads(handle.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("capabilities manifest must be an object")
    return value


def _open_runtime_docker(ws: Workspace, *, session_label: str) -> RuntimeOpenOutcome:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound

    try:
        client = docker.from_env()
    except DockerException as exc:
        return RuntimeOpenOutcome(named_failure(RUNTIME_DAEMON_UNREACHABLE, exc))
    except Exception as exc:  # noqa: BLE001
        return RuntimeOpenOutcome(unmodelled_failure(exc))
    rejected = verify_runner_image(client, settings.sandbox_image)
    if rejected is not None:
        return RuntimeOpenOutcome(rejected)
    try:
        image = client.images.get(settings.sandbox_image)
        container = _create_hardened_container(
            client,
            command=[
                "/bin/sh",
                "-lc",
                "trap 'exit 0' TERM INT; while :; do sleep 3600; done",
            ],
            session_label=session_label,
        )
    except ImageNotFound as exc:
        return RuntimeOpenOutcome(named_failure(RUNTIME_IMAGE_MISSING, exc))
    except (APIError, DockerException) as exc:
        return RuntimeOpenOutcome(named_failure(RUNTIME_START_FAILED, exc))
    except Exception as exc:  # noqa: BLE001
        return RuntimeOpenOutcome(unmodelled_failure(exc))

    _IN_FLIGHT.add(container.id)
    try:
        try:
            ingress_bytes = _transport().ingest(container, ws.sendable, ws.dirs)
        except TransportError as exc:
            return RuntimeOpenOutcome(_transport_failure(exc))
        except (APIError, DockerException, OSError) as exc:
            return RuntimeOpenOutcome(named_failure(RUNTIME_TRANSPORT_FAILED, exc))
        try:
            capabilities = _read_capabilities(container)
            container.start()
        except (APIError, DockerException, OSError, ValueError, json.JSONDecodeError) as exc:
            return RuntimeOpenOutcome(named_failure(RUNTIME_START_FAILED, exc))
        return RuntimeOpenOutcome(
            RunResult("", "", 0, False, ingress_bytes=ingress_bytes),
            container_ref=container.id,
            image_digest=str(image.id),
            capabilities=capabilities,
        )
    finally:
        _IN_FLIGHT.discard(container.id)
        # A successful open transfers ownership to the service. Failed opens leave no cache.
        # `container_ref` is unavailable inside `finally`, so inspect the container state.
        try:
            container.reload()
            opened = container.status == "running"
        except Exception:  # noqa: BLE001
            opened = False
        if not opened:
            try:
                container.remove(force=True, v=True)
            except Exception:  # noqa: BLE001
                pass


async def open_runtime_workspace(ws: Workspace, *, session_label: str) -> RuntimeOpenOutcome:
    if settings.sandbox_kind != "docker":
        return RuntimeOpenOutcome(RunResult("", "", -1, False, error=SANDBOX_DISABLED))
    return await asyncio.to_thread(_open_runtime_docker, ws, session_label=session_label)


def _container_alive(container: Any) -> bool:
    try:
        container.reload()
        return container.status == "running"
    except Exception:  # noqa: BLE001
        return False


async def exec_runtime_command(
    container_ref: str,
    command: str,
    *,
    timeout_seconds: int,
    on_output: Callable[[str, str], Awaitable[None]] | None = None,
    cancel_requested: Callable[[], Awaitable[bool]] | None = None,
) -> RuntimeExecOutcome:
    """Run one command via docker exec while keeping the RuntimeSession container alive."""
    import docker
    from docker.errors import APIError, DockerException, NotFound

    try:
        client = await asyncio.to_thread(docker.from_env)
        container = await asyncio.to_thread(client.containers.get, container_ref)
    except NotFound as exc:
        return RuntimeExecOutcome(named_failure(RUNTIME_TRANSPORT_FAILED, exc))
    except (DockerException, OSError) as exc:
        return RuntimeExecOutcome(_classify_daemon_failure(exc))
    except Exception as exc:  # noqa: BLE001
        return RuntimeExecOutcome(unmodelled_failure(exc))

    try:
        exec_id = await asyncio.to_thread(
            lambda: client.api.exec_create(
                container.id,
                ["/bin/sh", "-lc", command],
                workdir=WORK_DIR,
                user=f"{RUNNER_UID}:{RUNNER_GID}",
            )["Id"]
        )
        stream = await asyncio.to_thread(client.api.exec_start, exec_id, stream=True, demux=True)
    except (APIError, DockerException, OSError) as exc:
        return RuntimeExecOutcome(_classify_daemon_failure(exc))
    except Exception as exc:  # noqa: BLE001
        return RuntimeExecOutcome(unmodelled_failure(exc))

    output_queue: queue.Queue[tuple[str, bytes] | BaseException | None] = queue.Queue()

    def produce() -> None:
        try:
            for stdout_chunk, stderr_chunk in stream:
                if stdout_chunk:
                    output_queue.put(("stdout", stdout_chunk))
                if stderr_chunk:
                    output_queue.put(("stderr", stderr_chunk))
        except BaseException as exc:  # noqa: BLE001 - delivered to the async consumer
            output_queue.put(exc)
        finally:
            output_queue.put(None)

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    started = time.monotonic()
    stdout = bytearray()
    stderr = bytearray()
    truncated = False
    timed_out = False
    cancelled = False
    stop_requested_at: float | None = None
    last_cancel_check = 0.0
    stream_error: BaseException | None = None

    while True:
        now = time.monotonic()
        if (
            not cancelled
            and not timed_out
            and cancel_requested is not None
            and now - last_cancel_check >= 1.0
            and await cancel_requested()
        ):
            cancelled = True
            stop_requested_at = now
            await asyncio.to_thread(_kill_quietly, container)
        if cancel_requested is not None and now - last_cancel_check >= 1.0:
            last_cancel_check = now
        if not cancelled and not timed_out and now - started >= timeout_seconds:
            timed_out = True
            stop_requested_at = now
            await asyncio.to_thread(_kill_quietly, container)
        if stop_requested_at is not None and now - stop_requested_at > 5:
            break
        try:
            item = await asyncio.to_thread(output_queue.get, True, 0.1)
        except queue.Empty:
            continue
        if item is None:
            break
        if isinstance(item, BaseException):
            stream_error = item
            continue
        stream_name, chunk = item
        target = stdout if stream_name == "stdout" else stderr
        remaining = max(0, OUTPUT_MAX_BYTES - len(target))
        kept = chunk[:remaining]
        if kept:
            target.extend(kept)
            if on_output is not None:
                for offset in range(0, len(kept), 8192):
                    await on_output(
                        stream_name, kept[offset : offset + 8192].decode("utf-8", "replace")
                    )
        if len(kept) < len(chunk):
            truncated = True

    await asyncio.to_thread(producer.join, 5)
    if stream_error is not None and not (cancelled or timed_out):
        return RuntimeExecOutcome(
            _classify_daemon_failure(stream_error),
            container_alive=await asyncio.to_thread(_container_alive, container),
        )

    exit_code = -1
    if not (cancelled or timed_out):
        try:
            info = await asyncio.to_thread(client.api.exec_inspect, exec_id)
            exit_code = int(info.get("ExitCode", -1))
        except Exception as exc:  # noqa: BLE001
            return RuntimeExecOutcome(
                _classify_daemon_failure(exc),
                container_alive=await asyncio.to_thread(_container_alive, container),
            )

    try:
        await asyncio.to_thread(container.reload)
        oom = bool(container.attrs.get("State", {}).get("OOMKilled", False))
    except Exception:  # noqa: BLE001
        oom = False

    try:
        files = await asyncio.to_thread(_transport().egress, container)
    except TransportError as exc:
        return RuntimeExecOutcome(
            RunResult(
                stdout.decode("utf-8", "replace"),
                stderr.decode("utf-8", "replace"),
                exit_code,
                timed_out,
                error=exc.code,
                error_detail=exc.detail[:DETAIL_MAX],
                output_truncated=truncated,
            ),
            container_alive=await asyncio.to_thread(_container_alive, container),
            cancelled=cancelled,
        )
    except (APIError, DockerException, OSError) as exc:
        return RuntimeExecOutcome(
            named_failure(RUNTIME_TRANSPORT_FAILED, exc),
            container_alive=await asyncio.to_thread(_container_alive, container),
            cancelled=cancelled,
        )

    return RuntimeExecOutcome(
        RunResult(
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
            exit_code,
            timed_out,
            error=MEM_LIMIT if oom else None,
            output_truncated=truncated,
        ),
        files=files,
        container_alive=await asyncio.to_thread(_container_alive, container),
        cancelled=cancelled,
    )


def _snapshot_runtime_docker(container_ref: str) -> dict[str, WorkspaceFile]:
    import docker

    client = docker.from_env()
    container = client.containers.get(container_ref)
    _IN_FLIGHT.add(container.id)
    try:
        return _transport().egress(container)
    finally:
        _IN_FLIGHT.discard(container.id)


async def snapshot_runtime_workspace(container_ref: str) -> dict[str, WorkspaceFile]:
    try:
        return await asyncio.to_thread(_snapshot_runtime_docker, container_ref)
    except Exception as exc:  # noqa: BLE001 - service consumes the named boundary failure
        raise ScratchError(RUNTIME_TRANSPORT_FAILED) from exc


def _remove_runtime_docker(container_ref: str) -> None:
    import docker

    try:
        container = docker.from_env().containers.get(container_ref)
        container.remove(force=True, v=True)
    except Exception:  # noqa: BLE001 - cache cleanup is idempotent/best-effort
        pass
    _IN_FLIGHT.discard(container_ref)


async def remove_runtime_container(container_ref: str | None) -> None:
    if not container_ref:
        return
    await asyncio.to_thread(_remove_runtime_docker, container_ref)


# --- orphan sweep -----------------------------------------------------------


def deployment_owner_id() -> str:
    """A stable id for **this deployment**, used to scope container cleanup.

    Requirements it has to meet, and how:

    * **Stable across restarts** — otherwise a restarted worker could not recognise the
      containers its previous life leaked, and orphans would accumulate forever.
    * **Distinct per deployment, automatically** — including the test harness running beside
      a live dev worker on the same Docker daemon, which is exactly the configuration that
      produced the confirmed 409s. This is derived rather than declared: ADR-044 already
      gives the test harness its own database, so seeding the id from the data-plane
      identity makes tests distinct **without anyone remembering to set anything**. A
      forgettable step would have been a worse fix than the bug.
    * **Overridable** — ``SANDBOX_OWNER_ID`` wins when set, for anyone running two
      deployments against one database or otherwise needing to say it explicitly.
    """
    explicit = (settings.sandbox_owner_id or "").strip()
    if explicit:
        return explicit
    seed = f"{settings.database_url}|{settings.minio_bucket}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _container_age_seconds(container: Any, *, now: float) -> float | None:
    """Age from the label the creating process wrote, or ``None`` when unknowable.

    Our own label is preferred over the daemon's ``Created`` because it is written by the
    same clock the sweeper compares against. ``None`` means "cannot tell", and the caller
    treats that as *not* reclaimable — an unreadable age must never license a deletion."""
    try:
        labels = container.labels or {}
    except Exception:  # noqa: BLE001
        return None
    raw = labels.get(STARTED_LABEL)
    if raw is None:
        return None
    try:
        return max(0.0, now - float(raw))
    except (TypeError, ValueError):
        return None


def _is_reclaimable(
    container: Any,
    *,
    now: float,
    threshold: float,
    protected_ids: frozenset[str] = frozenset(),
) -> bool:
    """Whether a container owned by this deployment may be removed.

    Two guards, deliberately in this order:

    1. **Never touch what this process is using.** Exact, and covers the whole lifecycle —
       including the ``created`` state, which is where a container sits while a large
       workspace is being uploaded into it. That window is precisely where the observed
       ``409 container is dead or marked for removal`` came from.
    2. **Otherwise, require the container to be older than any legitimate run could be.**
       A live run cannot exceed ``SANDBOX_RUN_TIMEOUT_SECONDS`` (the orchestrator kills it)
       plus the bounded post-exit tail, so anything older has no orchestrator behind it.
       This holds regardless of container state, which is why a stopped-but-recent container
       is also spared: its creator may still be reading logs or pulling the egress tar out
       of it.

    An unknown age is treated as "not reclaimable": leaking a container costs disk, deleting
    a live one costs a user's run.
    """
    try:
        if container.id in _IN_FLIGHT or container.id in protected_ids:
            return False
    except Exception:  # noqa: BLE001
        return False
    age = _container_age_seconds(container, now=now)
    if age is None:
        return False
    return age > threshold


def sweep_orphan_containers(*, protected_ids: frozenset[str] = frozenset()) -> int:
    """Remove sandbox containers this deployment leaked (crashed worker, killed process).

    Containers are rebuildable caches — removing one never loses a persisted boundary — but
    removing the *wrong* one aborts somebody's live run, so the filter is ownership-scoped
    (``sherpa.owner``) and liveness-guarded (see :func:`_is_reclaimable`). Returns the count
    removed.

    **Running orphans are included**, which is required: a crashed worker can leave a
    container executing forever. They are reclaimed once they pass the age threshold rather
    than immediately, so recovery is *eventual* and never races an active run — with the
    default 120 s run timeout that is about 7 minutes.
    """
    if settings.sandbox_kind != "docker":
        return 0
    owner = deployment_owner_id()
    try:
        import docker

        client = docker.from_env()
        containers = client.containers.list(all=True, filters={"label": f"{OWNER_LABEL}={owner}"})
    except Exception:  # noqa: BLE001 - best-effort cache cleanup, never fatal to startup
        return 0
    now = time.time()
    threshold = float(settings.sandbox_run_timeout_seconds) + SWEEP_GRACE_SECONDS
    removed = 0
    for c in containers:
        if not _is_reclaimable(c, now=now, threshold=threshold, protected_ids=protected_ids):
            continue
        try:
            c.remove(force=True, v=True)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed
