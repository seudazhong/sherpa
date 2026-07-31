"""Workspace transport for the coding sandbox — tar ingress/egress (ADR-047 §决策1/5).

**There is no bind mount and no host path in the container-create call at all.** The
orchestrator builds an in-memory tar of the one-time disposable copy of the working copy,
``put_archive``s it into the container's **anonymous** ``/work`` volume, and reads the
result back with ``get_archive``. Because there is no ``src=`` parameter, ADR-039 §决策1③'s
requirement to validate a constructed scratch source path as untrusted input no longer
applies — the attack surface is structurally removed rather than guarded.

Three rules this module enforces:

* **No credential ever enters the tar** (config §1.7). ``.env*``, ``*.pem``, ``*.key``,
  ``id_*`` and ``.git/config`` are held back from the archive and then *asserted absent*
  before the bytes leave this module. Held-back paths stay in the working copy untouched —
  they are invisible to the sandbox, not deleted by it.
* **The egress tar is untrusted input.** It is expanded in bounded memory reusing the
  audited semantics of :mod:`app.services.archive` (the same expander the Project import
  path uses): absolute paths, ``..`` traversal, NUL, device/FIFO/block nodes, hard links
  and symlinks resolving outside the root are rejected → ``path_escape``.
* **Both directions are bounded** by ``SANDBOX_SCRATCH_MAX_BYTES``, and egress is bounded
  *before* it allocates: the archive is streamed rather than buffered, and a member is
  refused on its declared size before a byte of it is copied. Overflow is a named exit,
  never a silent truncation.

**Known semantic loss: symlinks do not round-trip.** ``materialize`` writes a symlink as a
regular file containing its target text, and egress drops symlink members entirely (they
carry no persistable bytes, and the overlay has no symlink representation). So a symlink in
the project is visible to the command as an ordinary file, and a symlink the *command*
creates is silently not persisted. This is a deliberate safety trade — never materializing a
real link means no link can be followed out of the tree — but it is a real limitation and is
recorded here rather than implied away. Restoring fidelity would need an overlay entry kind,
which is a data-model change and therefore out of P3's scope.

The ``work/`` prefix that ``get_archive`` puts on every member is stripped **before**
validation — validating with the prefix still attached would make a top-level escaping
symlink (``a -> ../evil``) resolve *inside* the archive root and pass.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import io
import posixpath
import tarfile
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

# The audited, bounded expander already used for Project archive import (config §1.7:
# "reusing the bounded expander already used for project imports"). These helpers are the
# single implementation of Sherpa's unsafe-member semantics; re-deriving them here would be
# exactly the kind of drift ADR-045 is trying to remove.
from app.services.archive import (
    ArchiveError,
    _normalize_path,  # noqa: PLC2701 - deliberate reuse of the audited semantics
    _validate_symlink,  # noqa: PLC2701
)

#: Where the workspace lives inside the container. The runner image declares it as a
#: ``VOLUME``, so it is an **anonymous volume** — writable even though the rootfs is
#: read-only, and removed with the container (``remove(v=True)``).
WORK_DIR = "/work"

#: The uid/gid baked into ``sandbox-runner/Dockerfile``. Tar members are written with it so
#: the non-root runner owns every file and directory it receives (docker's implicit parent
#: directories would otherwise be created as root and be unwritable).
RUNNER_UID = 10001
RUNNER_GID = 10001

#: Fixed member mtime: the archive is a deterministic function of the workspace content.
_MTIME = 0

#: Paths never sent into the sandbox (config §1.7 credential boundary). Matched against the
#: **basename**, so ``sub/dir/.env`` and ``keys/id_rsa`` are caught too.
CREDENTIAL_BASENAME_PATTERNS = (".env*", "*.pem", "*.key", "id_*")

MAX_PATH_DEPTH = 64
MAX_PATH_LENGTH = 1024
MAX_ENTRIES = 100_000

#: Bytes copied per read when draining a member. Peak memory is dominated by the retained
#: workspace, not by this, but it keeps a single huge member from being pulled in one slab.
COPY_CHUNK_BYTES = 256 * 1024

#: Read-ahead `tarfile` is allowed while parsing the stream. Must be a multiple of 512. This
#: is the only *unconditional* allocation in the egress path — it is a constant, not a
#: fraction of the archive, which is precisely the property the bounded-egress tests assert.
TAR_STREAM_BUFSIZE = 32 * 1024

#: How much the wire reader may hold beyond the caller's budget while it looks for the end
#: of the archive. Small and constant: the reader stops as soon as the budget is exceeded.
_WIRE_SLACK_BYTES = 1024 * 1024


class TransportError(Exception):
    """A named, non-leaking transport failure. ``code`` is a contract termination reason."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class WorkspaceFile:
    """One regular file in the disposable workspace copy.

    ``executable`` is the only permission bit that survives into the overlay and the change
    set (``project_working_copy_entries.executable``), so it — not ``mode`` — is what the
    delta compares. ``mode`` is carried for round-trip fidelity so a file that came back from
    the container with, say, ``0o600`` is not silently rewritten to ``0o644`` on the next
    ingress.
    """

    data: bytes
    executable: bool = False
    mode: int | None = None

    def effective_mode(self) -> int:
        """The mode to write into the tar: the observed one when known, else the default for
        this file's executable bit."""
        if self.mode is not None:
            return self.mode
        return 0o755 if self.executable else 0o644


def is_credential_path(path: str) -> bool:
    """True when ``path`` must never cross the sandbox boundary (config §1.7).

    Deliberately broad: ``id_*`` also matches innocent names such as ``id_utils.py``. The
    cost is bounded — a held-back file stays in the working copy and is simply invisible to
    the sandbox — and the contract states the pattern literally, so it is implemented
    literally rather than silently narrowed.
    """
    name = posixpath.basename(path)
    if any(fnmatch.fnmatchcase(name, pat) for pat in CREDENTIAL_BASENAME_PATTERNS):
        return True
    parts = path.split("/")
    return len(parts) >= 2 and parts[-2] == ".git" and parts[-1] == "config"


class WorkspaceTransport(Protocol):
    """How a workspace copy reaches a runtime and comes back. ADR-047 §决策6's evolution
    path (named volume → remote runner) swaps the implementation, not the caller."""

    def ingest(
        self, container: Any, files: Mapping[str, WorkspaceFile], dirs: Iterable[str]
    ) -> int: ...

    def egress(self, container: Any) -> dict[str, WorkspaceFile]: ...


class TarTransport:
    """``put_archive`` / ``get_archive`` over an in-memory tar. No host path, no mount."""

    def __init__(self, *, max_bytes: int) -> None:
        self._max_bytes = max_bytes

    # --- ingress ------------------------------------------------------------

    def build(self, files: Mapping[str, WorkspaceFile], dirs: Iterable[str]) -> bytes:
        """Serialize the workspace copy. Raises ``TransportError('credential_leak')`` if a
        credential-shaped path ever reaches this point — a defensive assertion on top of the
        caller's strip, so a future refactor cannot quietly reopen the boundary.

        **Every ancestor directory is emitted explicitly**, owned by the runner uid. Left
        implicit, docker's extraction creates them as root and the non-root runner then
        cannot write inside them — a failure that only shows up in a real container, which
        is exactly what the ``-m docker`` lane exists to catch.
        """
        buf = io.BytesIO()
        total = 0
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for path in sorted(_all_parents(files, dirs)):
                if is_credential_path(path):
                    raise TransportError("credential_leak", "credential-shaped directory")
                info = tarfile.TarInfo(path)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = _MTIME
                info.uid = RUNNER_UID
                info.gid = RUNNER_GID
                tf.addfile(info)
            for path in sorted(files):
                if is_credential_path(path):
                    raise TransportError("credential_leak", "credential-shaped file")
                f = files[path]
                total += len(f.data)
                if total > self._max_bytes:
                    raise TransportError("scratch_too_large", "ingress cap exceeded")
                info = tarfile.TarInfo(path)
                info.size = len(f.data)
                info.mode = f.effective_mode()
                info.mtime = _MTIME
                info.uid = RUNNER_UID
                info.gid = RUNNER_GID
                tf.addfile(info, io.BytesIO(f.data))
        raw = buf.getvalue()
        if len(raw) > self._max_bytes:
            raise TransportError("scratch_too_large", "ingress archive exceeds the transfer cap")
        return raw

    def ingest(
        self, container: Any, files: Mapping[str, WorkspaceFile], dirs: Iterable[str]
    ) -> int:
        raw = self.build(files, dirs)
        container.put_archive(WORK_DIR, raw)
        return len(raw)

    # --- egress -------------------------------------------------------------

    def egress(self, container: Any) -> dict[str, WorkspaceFile]:
        """Stream `/work` back out of the container under a hard peak-memory budget.

        The archive is **never** materialized as a single object: the chunk iterator is
        adapted into a non-seekable file object, `tarfile` reads it in streaming mode, and
        each member is copied out in bounded pieces. Nothing is allocated for a member until
        its declared size has been checked against the remaining budget.
        """
        stream, _stat = container.get_archive(WORK_DIR)
        return self._expand_stream(_ChunkStream(stream, max_bytes=self._wire_cap()))

    def expand(self, raw: bytes) -> dict[str, WorkspaceFile]:
        """Expand an already-materialized egress tar (tests, and any caller that legitimately
        holds the bytes). Shares one implementation with :meth:`egress`, so the bounds and the
        unsafe-member rules cannot drift between the two entry points."""
        if len(raw) > self._wire_cap():
            raise TransportError("scratch_too_large", "egress archive exceeds the transfer cap")
        return self._expand_stream(io.BytesIO(raw))

    def _wire_cap(self) -> int:
        """Wire bytes are capped by the same budget as content. A tar only ever *adds* to
        what it carries, so bounding the wire strictly bounds the content too — and it is
        the wire that determines peak memory while reading."""
        return self._max_bytes

    def _expand_stream(self, fileobj: Any) -> dict[str, WorkspaceFile]:
        """Expand an **untrusted** egress tar into validated regular files.

        Directory and symlink members carry no persistable bytes and are dropped (the
        materializer writes a symlink as a regular file recording its target, so a symlink
        the *command* created has no round-trip representation — see the module docstring).
        Credential-shaped paths the command may have created are dropped too: they never
        enter the change set.

        ``mode="r|"`` is deliberate and load-bearing: it is **uncompressed streaming only**.
        Streaming means the whole archive is never held at once; refusing compression means a
        decompression bomb cannot expand past the budget between two size checks. Docker's
        ``get_archive`` always returns an uncompressed tar, so nothing legitimate is lost.
        """
        files: dict[str, WorkspaceFile] = {}
        total = 0
        entries = 0
        try:
            with tarfile.open(fileobj=fileobj, mode="r|", bufsize=TAR_STREAM_BUFSIZE) as tf:
                for member in tf:
                    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                        raise TransportError("path_escape", "device/fifo node")
                    if member.islnk():
                        raise TransportError("path_escape", "hard link")
                    if member.type == tarfile.GNUTYPE_SPARSE:
                        # A sparse member's on-wire size understates what it expands to; the
                        # budget check below would be reasoning about the wrong number.
                        raise TransportError("path_escape", "sparse member")
                    rel = _strip_work_prefix(member.name)
                    if rel is None:
                        continue  # the '/work' root member itself
                    entries += 1
                    if entries > MAX_ENTRIES:
                        raise TransportError("scratch_too_large", "entry count cap exceeded")
                    # Normalize/validate on the STRIPPED path: with the 'work/' prefix still
                    # attached, `a -> ../evil` would resolve inside the archive and pass.
                    path = _normalize_path(
                        rel, depth_cap=MAX_PATH_DEPTH, length_cap=MAX_PATH_LENGTH
                    )
                    if member.isdir():
                        continue
                    if member.issym():
                        _validate_symlink(path, member.linkname)
                        continue
                    if not member.isfile():
                        raise TransportError("path_escape", "unsupported member type")

                    # BEFORE any allocation: refuse a member that cannot fit in what is left.
                    remaining = self._max_bytes - total
                    if member.size > remaining:
                        raise TransportError("scratch_too_large", "egress cap exceeded")
                    data = _drain_member(tf, member, remaining)
                    total += len(data)
                    if is_credential_path(path):
                        continue
                    files[path] = WorkspaceFile(
                        data=data, executable=bool(member.mode & 0o111), mode=member.mode & 0o7777
                    )
        except ArchiveError as exc:
            raise TransportError("path_escape", str(exc)) from exc
        except TransportError:
            raise
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise TransportError("runtime_transport_failed", str(exc)) from exc
        return files


def _drain_member(tf: tarfile.TarFile, member: tarfile.TarInfo, budget: int) -> bytes:
    """Copy one member out with a **single**, exactly-sized allocation.

    The declared size was already checked against the budget, but it is attacker-supplied
    metadata, so this also refuses a member that turns out to carry more than it declared.
    Reading into a preallocated buffer (rather than growing one and copying) is what keeps
    peak memory at one member rather than a multiple of the archive."""
    fh = tf.extractfile(member)
    if fh is None:
        return b""
    size = member.size
    if size < 0 or size > budget:
        raise TransportError("scratch_too_large", "member exceeds the remaining budget")
    buf = bytearray(size)
    view = memoryview(buf)
    got = 0
    while got < size:
        try:
            n = fh.readinto(view[got:])  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover - defensive, tarfile provides readinto
            chunk = fh.read(min(COPY_CHUNK_BYTES, size - got))
            n = len(chunk)
            view[got : got + n] = chunk
        if not n:
            break
        got += n
    if got != size:
        raise TransportError("runtime_transport_failed", "truncated member")
    if fh.read(1):
        # The header understated the payload: reject rather than silently keep the prefix.
        raise TransportError("scratch_too_large", "member exceeded its declared size")
    return bytes(buf)


class _ChunkStream(io.RawIOBase):
    """A non-seekable, read-only file object over docker's ``get_archive`` chunk iterator.

    Two jobs: let `tarfile` stream (so the archive is never one object in memory), and stop
    pulling from the wire the moment the budget is exceeded — the cap is enforced *while*
    reading rather than after a full download."""

    def __init__(self, chunks: Any, *, max_bytes: int) -> None:
        super().__init__()
        self._chunks = iter(chunks)
        self._max_bytes = max_bytes
        self._buf = bytearray()
        self._pulled = 0
        self._eof = False

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def _pull(self) -> bool:
        try:
            chunk = next(self._chunks)
        except StopIteration:
            self._eof = True
            return False
        self._pulled += len(chunk)
        if self._pulled > self._max_bytes + _WIRE_SLACK_BYTES:
            raise TransportError("scratch_too_large", "egress archive exceeds the transfer cap")
        self._buf += chunk
        return True

    def readinto(self, b: Any) -> int:
        want = len(b)
        while len(self._buf) < want and not self._eof:
            if not self._pull():
                break
        take = min(want, len(self._buf))
        b[:take] = self._buf[:take]
        del self._buf[:take]
        return take


def _all_parents(files: Mapping[str, WorkspaceFile], dirs: Iterable[str]) -> set[str]:
    """Every directory the archive must create, including the ancestors of every file."""
    explicit = {d.strip("/") for d in dirs if d.strip("/")}
    out: set[str] = set(explicit)
    for path in [*files, *explicit]:
        parts = path.split("/")
        depth = len(parts) if path in explicit else len(parts) - 1
        for i in range(1, depth + 1):
            candidate = "/".join(parts[:i])
            if candidate:
                out.add(candidate)
    return out


def _strip_work_prefix(name: str) -> str | None:
    """``get_archive('/work')`` names every member ``work/...``; the root member is ``work``.

    Only an explicit ``./`` prefix is removed — never ``lstrip('./')``, which would eat the
    leading ``/`` of an absolute path and the leading ``..`` of a traversal, silently
    turning two rejected members into accepted ones.
    """
    cleaned = name.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    root = posixpath.basename(WORK_DIR)
    if cleaned in (root, root + "/"):
        return None
    prefix = root + "/"
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix) :]
    return cleaned or None
