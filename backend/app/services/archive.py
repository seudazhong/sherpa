"""Bounded, isolated archive expansion for Project archive import (ADR-037, W2a).

Untrusted archive uploads (ZIP / TAR / tar.gz) are expanded **in bounded memory**,
never extracted to a canonical location or the filesystem (so on-disk symlink/path
tricks cannot fire). Members are read one at a time under running size/count/ratio/
depth caps; unsafe members are rejected. The result is a list of validated, normalized
entries the caller materializes into an immutable snapshot (config §1.5 boundary):

- absolute / traversal (``..``) / NUL paths → rejected
- device / FIFO / block / char nodes, hard links → rejected
- symlinks that escape the project root (or are absolute) → rejected; safe relative
  symlinks are kept as ``symlink`` entries (no bytes)
- the client Content-Type / extension is never trusted; the format is sniffed
"""

from __future__ import annotations

import dataclasses
import io
import posixpath
import tarfile
import zipfile


class ArchiveError(Exception):
    """Named archive-expansion failure. ``code`` is a stable termination reason."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclasses.dataclass(frozen=True)
class ArchiveEntry:
    path: str
    entry_kind: str  # file | dir | symlink
    data: bytes | None = None
    symlink_target: str | None = None
    executable: bool = False


@dataclasses.dataclass(frozen=True)
class ArchiveBounds:
    max_expanded_bytes: int
    max_entries: int
    max_expansion_ratio: int
    max_path_depth: int
    max_path_length: int = 1024


def _normalize_path(raw: str, *, depth_cap: int, length_cap: int) -> str:
    """Return a safe, normalized POSIX relative path, or raise ArchiveError."""
    if not raw:
        raise ArchiveError("unsafe_archive", "empty path")
    if "\x00" in raw:
        raise ArchiveError("unsafe_archive", "NUL in path")
    candidate = raw.replace("\\", "/")
    if candidate.startswith("/"):
        raise ArchiveError("unsafe_archive", "absolute path")
    parts: list[str] = []
    for seg in candidate.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ArchiveError("unsafe_archive", "path traversal")
        parts.append(seg)
    if not parts:
        raise ArchiveError("unsafe_archive", "empty path after normalization")
    if len(parts) > depth_cap:
        raise ArchiveError("unsafe_archive", "path too deep")
    normalized = "/".join(parts)
    if len(normalized) > length_cap:
        raise ArchiveError("unsafe_archive", "path too long")
    return normalized


def _validate_symlink(entry_path: str, target: str) -> str:
    """A symlink target must be relative and stay within the project root."""
    if not target or "\x00" in target:
        raise ArchiveError("unsafe_archive", "bad symlink target")
    if target.replace("\\", "/").startswith("/"):
        raise ArchiveError("unsafe_archive", "absolute symlink target")
    base = posixpath.dirname(entry_path)
    resolved = posixpath.normpath(posixpath.join(base, target.replace("\\", "/")))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise ArchiveError("unsafe_archive", "escaping symlink")
    return target.replace("\\", "/")


class _Budget:
    def __init__(self, bounds: ArchiveBounds, compressed_bytes: int) -> None:
        self._bounds = bounds
        self._expanded = 0
        self._entries = 0
        self._ratio_cap = compressed_bytes * bounds.max_expansion_ratio

    def add_entry(self) -> None:
        self._entries += 1
        if self._entries > self._bounds.max_entries:
            raise ArchiveError("too_many_entries", "entry count cap exceeded")

    def add_bytes(self, n: int) -> None:
        self._expanded += n
        if self._expanded > self._bounds.max_expanded_bytes:
            raise ArchiveError("too_large", "expanded size cap exceeded")
        if self._expanded > self._ratio_cap:
            raise ArchiveError("expansion_ratio", "expansion ratio cap exceeded")


def _read_bounded(fileobj: io.BufferedReader | None, budget: _Budget) -> bytes:
    if fileobj is None:
        return b""
    chunks: list[bytes] = []
    while True:
        chunk = fileobj.read(65536)
        if not chunk:
            break
        budget.add_bytes(len(chunk))
        chunks.append(chunk)
    return b"".join(chunks)


def _expand_zip(raw: bytes, bounds: ArchiveBounds) -> list[ArchiveEntry]:
    budget = _Budget(bounds, len(raw))
    entries: list[ArchiveEntry] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or info.filename.endswith("/"):
                    budget.add_entry()
                    path = _normalize_path(
                        info.filename,
                        depth_cap=bounds.max_path_depth,
                        length_cap=bounds.max_path_length,
                    )
                    entries.append(ArchiveEntry(path=path, entry_kind="dir"))
                    continue
                path = _normalize_path(
                    info.filename,
                    depth_cap=bounds.max_path_depth,
                    length_cap=bounds.max_path_length,
                )
                budget.add_entry()
                if mode == 0o120000:  # symlink
                    with zf.open(info) as fh:
                        data = _read_bounded(fh, budget)  # type: ignore[arg-type]
                    target = _validate_symlink(path, data.decode("utf-8", "replace"))
                    entries.append(
                        ArchiveEntry(path=path, entry_kind="symlink", symlink_target=target)
                    )
                    continue
                with zf.open(info) as fh:
                    data = _read_bounded(fh, budget)  # type: ignore[arg-type]
                executable = bool((info.external_attr >> 16) & 0o111)
                entries.append(
                    ArchiveEntry(path=path, entry_kind="file", data=data, executable=executable)
                )
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError("bad_archive", str(exc)) from exc
    return entries


def _expand_tar(raw: bytes, bounds: ArchiveBounds) -> list[ArchiveEntry]:
    budget = _Budget(bounds, len(raw))
    entries: list[ArchiveEntry] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
            for member in tf:
                if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                    raise ArchiveError("unsafe_archive", "device/fifo node")
                if member.islnk():
                    raise ArchiveError("unsafe_archive", "hard link")
                path = _normalize_path(
                    member.name,
                    depth_cap=bounds.max_path_depth,
                    length_cap=bounds.max_path_length,
                )
                budget.add_entry()
                if member.isdir():
                    entries.append(ArchiveEntry(path=path, entry_kind="dir"))
                elif member.issym():
                    target = _validate_symlink(path, member.linkname)
                    entries.append(
                        ArchiveEntry(path=path, entry_kind="symlink", symlink_target=target)
                    )
                elif member.isfile():
                    fh = tf.extractfile(member)
                    data = _read_bounded(fh, budget)  # type: ignore[arg-type]
                    executable = bool(member.mode & 0o111)
                    entries.append(
                        ArchiveEntry(path=path, entry_kind="file", data=data, executable=executable)
                    )
                else:
                    raise ArchiveError("unsafe_archive", "unsupported member type")
    except ArchiveError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError("bad_archive", str(exc)) from exc
    return entries


def expand_archive(raw: bytes, bounds: ArchiveBounds) -> list[ArchiveEntry]:
    """Sniff + bounded-expand an archive into validated entries. Format is detected
    from content (never the extension / Content-Type). Raises ArchiveError with a
    named ``code`` on any unsafe / over-bounds condition."""
    if not raw:
        raise ArchiveError("bad_archive", "empty upload")
    if zipfile.is_zipfile(io.BytesIO(raw)):
        return _expand_zip(raw, bounds)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*"):
            pass
    except tarfile.TarError as exc:
        raise ArchiveError("bad_archive", "unrecognized archive format") from exc
    return _expand_tar(raw, bounds)
