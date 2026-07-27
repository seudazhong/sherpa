"""Bounded archive expansion safety (ADR-037, W2a; config §1.5).

Pure unit tests (no DB): valid ZIP/TAR expand to normalized entries; every unsafe or
over-bounds condition is a named ArchiveError.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from app.services.archive import ArchiveBounds, ArchiveError, expand_archive

_BOUNDS = ArchiveBounds(
    max_expanded_bytes=10_000_000,
    max_entries=1000,
    max_expansion_ratio=200,
    max_path_depth=10,
)


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def _zip_symlink(link_name: str, target: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(link_name)
        info.external_attr = (0o120777 & 0xFFFF) << 16  # symlink mode
        zf.writestr(info, target)
    return buf.getvalue()


def test_zip_happy_path() -> None:
    raw = _zip([("README.md", b"# hi"), ("src/main.py", b"print(1)")])
    entries = expand_archive(raw, _BOUNDS)
    paths = {e.path: e for e in entries}
    assert paths["README.md"].entry_kind == "file"
    assert paths["README.md"].data == b"# hi"
    assert paths["src/main.py"].entry_kind == "file"
    # Parent dir is synthesized by the caller (service), not here; the file path is kept.


def test_zip_rejects_traversal() -> None:
    raw = _zip([("../escape.txt", b"x")])
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, _BOUNDS)
    assert exc.value.code == "unsafe_archive"


def test_zip_rejects_absolute() -> None:
    raw = _zip([("/etc/passwd", b"x")])
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, _BOUNDS)
    assert exc.value.code == "unsafe_archive"


def test_zip_rejects_escaping_symlink() -> None:
    raw = _zip_symlink("link", "../../etc/passwd")
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, _BOUNDS)
    assert exc.value.code == "unsafe_archive"


def test_zip_allows_safe_relative_symlink() -> None:
    raw = _zip_symlink("docs/here", "readme.md")
    entries = expand_archive(raw, _BOUNDS)
    sym = next(e for e in entries if e.entry_kind == "symlink")
    assert sym.symlink_target == "readme.md"


def test_too_many_entries() -> None:
    tiny = ArchiveBounds(
        max_expanded_bytes=10_000_000, max_entries=2, max_expansion_ratio=200, max_path_depth=10
    )
    raw = _zip([("a", b"1"), ("b", b"2"), ("c", b"3")])
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, tiny)
    assert exc.value.code == "too_many_entries"


def test_expanded_too_large() -> None:
    tiny = ArchiveBounds(
        max_expanded_bytes=10, max_entries=1000, max_expansion_ratio=100000, max_path_depth=10
    )
    raw = _zip([("big.txt", b"x" * 5000)])
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, tiny)
    assert exc.value.code == "too_large"


def test_expansion_ratio_bomb() -> None:
    ratio = ArchiveBounds(
        max_expanded_bytes=10_000_000, max_entries=1000, max_expansion_ratio=2, max_path_depth=10
    )
    raw = _zip([("z.txt", b"0" * 100_000)])  # highly compressible → high ratio
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, ratio)
    assert exc.value.code == "expansion_ratio"


def test_path_too_deep() -> None:
    shallow = ArchiveBounds(
        max_expanded_bytes=10_000_000, max_entries=1000, max_expansion_ratio=200, max_path_depth=2
    )
    raw = _zip([("a/b/c/d.txt", b"x")])
    with pytest.raises(ArchiveError) as exc:
        expand_archive(raw, shallow)
    assert exc.value.code == "unsafe_archive"


def test_tar_happy_and_rejects_hardlink() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"hello"
        info = tarfile.TarInfo("file.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    entries = expand_archive(buf.getvalue(), _BOUNDS)
    assert any(e.path == "file.txt" and e.entry_kind == "file" for e in entries)

    bomb = io.BytesIO()
    with tarfile.open(fileobj=bomb, mode="w") as tf:
        info = tarfile.TarInfo("hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "file.txt"
        tf.addfile(info)
    with pytest.raises(ArchiveError) as exc:
        expand_archive(bomb.getvalue(), _BOUNDS)
    assert exc.value.code == "unsafe_archive"


def test_tar_rejects_device_node() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("dev")
        info.type = tarfile.CHRTYPE
        tf.addfile(info)
    with pytest.raises(ArchiveError) as exc:
        expand_archive(buf.getvalue(), _BOUNDS)
    assert exc.value.code == "unsafe_archive"


def test_not_an_archive() -> None:
    with pytest.raises(ArchiveError) as exc:
        expand_archive(b"this is not an archive at all", _BOUNDS)
    assert exc.value.code == "bad_archive"
