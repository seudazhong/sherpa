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
* **Both directions are bounded** by ``SANDBOX_SCRATCH_MAX_BYTES``; overflow is a named
  exit, never a silent truncation.

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
MAX_ENTRIES = 200_000


class TransportError(Exception):
    """A named, non-leaking transport failure. ``code`` is a contract termination reason."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class WorkspaceFile:
    """One regular file in the disposable workspace copy."""

    data: bytes
    executable: bool = False


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
                info.mode = 0o755 if f.executable else 0o644
                info.mtime = _MTIME
                info.uid = RUNNER_UID
                info.gid = RUNNER_GID
                tf.addfile(info, io.BytesIO(f.data))
        return buf.getvalue()

    def ingest(
        self, container: Any, files: Mapping[str, WorkspaceFile], dirs: Iterable[str]
    ) -> int:
        raw = self.build(files, dirs)
        container.put_archive(WORK_DIR, raw)
        return len(raw)

    # --- egress -------------------------------------------------------------

    def egress(self, container: Any) -> dict[str, WorkspaceFile]:
        stream, _stat = container.get_archive(WORK_DIR)
        chunks: list[bytes] = []
        total = 0
        for chunk in stream:
            total += len(chunk)
            if total > self._max_bytes:
                raise TransportError("scratch_too_large", "egress cap exceeded")
            chunks.append(chunk)
        return self.expand(b"".join(chunks))

    def expand(self, raw: bytes) -> dict[str, WorkspaceFile]:
        """Expand an **untrusted** egress tar into validated regular files.

        Directory and symlink members carry no persistable bytes and are dropped (the
        materializer writes a symlink as a regular file recording its target, so a symlink
        the *command* created has no round-trip representation). Credential-shaped paths the
        command may have created are dropped too: they never enter the change set.
        """
        files: dict[str, WorkspaceFile] = {}
        total = 0
        entries = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                for member in tf:
                    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                        raise TransportError("path_escape", "device/fifo node")
                    if member.islnk():
                        raise TransportError("path_escape", "hard link")
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
                    fh = tf.extractfile(member)
                    data = fh.read() if fh is not None else b""
                    total += len(data)
                    if total > self._max_bytes:
                        raise TransportError("scratch_too_large", "egress cap exceeded")
                    if is_credential_path(path):
                        continue
                    files[path] = WorkspaceFile(data=data, executable=bool(member.mode & 0o111))
        except ArchiveError as exc:
            raise TransportError("path_escape", str(exc)) from exc
        except TransportError:
            raise
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise TransportError("runtime_transport_failed", str(exc)) from exc
        return files


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
