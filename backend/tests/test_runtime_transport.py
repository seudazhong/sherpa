"""Tar workspace transport for the coding sandbox (Phase TR P3, ADR-047).

Four guarantees, each with its own section below:

1. **No bind mount, no host path.** The container-create call carries no ``mounts``,
   no ``binds`` and no filesystem path — this is the structural fix for backlog B-8, whose
   root cause was a *worker-container* path handed to the *host* daemon.
2. **Round trip fidelity.** Content, executable bits and nested paths survive
   ``put_archive`` → command → ``get_archive``.
3. **Credential boundary (config §1.7).** A secret-shaped file in the project tree never
   enters the tar, the delta, the change set or any observation — and holding it back is
   never mistaken for the sandbox *deleting* it.
4. **The egress tar is untrusted input.** zip-slip, ``..``, NUL, device/FIFO nodes, hard
   links and escaping symlinks are rejected with a named reason.
"""

from __future__ import annotations

import hashlib
import io
import tarfile

import pytest

from app.config import settings
from app.sandbox import runtime as sbx
from app.sandbox.transport import (
    RUNNER_UID,
    TarTransport,
    TransportError,
    WorkspaceFile,
    is_credential_path,
)
from tests.fake_docker import FakeSpec, patch_docker, tar_to_files

# A KEK-shaped canary: the exact thing config §1.7 says must never cross the boundary.
CANARY = "sherpa-kek-canary-MDEyMzQ1Njc4OWFiY2RlZg=="


def _entry(path: str, data: bytes, *, executable: bool = False) -> sbx.MaterializeEntry:
    return sbx.MaterializeEntry(
        path=path,
        entry_kind="file",
        content_hash=hashlib.sha256(data).digest(),
        size_bytes=len(data),
        executable=executable,
        symlink_target=None,
    )


async def _materialize(entries: list[sbx.MaterializeEntry], blobs: dict[bytes, bytes]):  # type: ignore[no-untyped-def]
    async def _read(h: bytes) -> bytes:
        return blobs[h]

    return await sbx.materialize(entries, _read)


async def _ws(files: dict[str, bytes], **kw: object):  # type: ignore[no-untyped-def]
    blobs = {hashlib.sha256(d).digest(): d for d in files.values()}
    entries = [_entry(p, d, **kw) for p, d in files.items()]  # type: ignore[arg-type]
    return await _materialize(entries, blobs)


# --- 1. no bind mount, no host path -----------------------------------------


async def test_container_create_passes_no_mount_and_no_host_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """B-8 root-cause regression: the create call must contain no path-shaped argument."""
    client = patch_docker(monkeypatch, FakeSpec(stdout=b"ok\n"))
    assert client is not None
    ws = await _ws({"a.txt": b"alpha\n"})
    await sbx.run_workspace(ws, "true")

    kwargs = client.containers.create_kwargs
    assert "mounts" not in kwargs
    assert "binds" not in kwargs
    assert "volumes" not in kwargs
    # /work is the runner image's anonymous volume; the only path we name is the workdir.
    assert kwargs["working_dir"] == "/work"
    # The ADR-025 hardening is unchanged.
    assert kwargs["network_disabled"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["read_only"] is True
    assert kwargs["user"] == f"{RUNNER_UID}:{RUNNER_UID}"
    assert "nosuid" in kwargs["tmpfs"]["/tmp"] and "nodev" in kwargs["tmpfs"]["/tmp"]
    # No credential is ever injected into the container environment (ADR-019/025).
    assert "environment" not in kwargs and "env" not in kwargs


def test_no_bind_mount_remains_anywhere_in_the_sandbox_package() -> None:
    """A canary against regression: no bind mount may come back into ``app/sandbox/``.

    Docstrings and comments are stripped first — the module *explains* the deleted
    ``Mount(type="bind", ...)`` on purpose, and a naive substring check would either forbid
    that explanation or be defeated by rewording it.
    """
    import io as _io
    import pathlib
    import token as _token
    import tokenize as _tokenize

    pkg = pathlib.Path(sbx.__file__).parent
    for path in sorted(pkg.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        code = "".join(
            tok.string
            for tok in _tokenize.generate_tokens(_io.StringIO(src).readline)
            if tok.type not in (_token.STRING, _tokenize.COMMENT)
        )
        assert "bind" not in code, path
        assert "Mount" not in code, path
        assert "docker.types" not in code, path


async def test_disabled_sandbox_still_edits_and_deltas() -> None:
    """ADR-048 §决策2: losing *run* must never mean losing *edit*."""
    settings_kind = settings.sandbox_kind
    assert settings_kind != "docker"  # test default
    ws = await _ws({"README.md": b"# base\n"})
    sbx.apply_edit(ws, sbx.ScratchEdit(path="new.txt", op="write", data=b"hi\n"))
    outcome = await sbx.run_workspace(ws, "pytest -q")
    assert outcome.result.error == sbx.SANDBOX_DISABLED
    assert outcome.files is None
    delta = sbx.compute_delta(ws, ws.files)
    assert {e.path: e.change_kind for e in delta.entries} == {"new.txt": "added"}


# --- 2. round trip ----------------------------------------------------------


def test_tar_round_trip_preserves_content_and_mode_bits() -> None:
    t = TarTransport(max_bytes=1_000_000)
    files = {
        "a.txt": WorkspaceFile(b"alpha\n"),
        "src/run.sh": WorkspaceFile(b"#!/bin/sh\necho hi\n", executable=True),
        "deep/nested/dir/file.bin": WorkspaceFile(b"\x00\x01\x02"),
    }
    raw = t.build(files, {"src", "deep/nested/dir"})
    back = tar_to_files(raw)
    assert back == files
    # Every member is owned by the non-root runner uid, so implicit parents are writable.
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
        assert {m.uid for m in tf.getmembers()} == {RUNNER_UID}
        assert {m.name for m in tf.getmembers() if m.isdir()} == {"src", "deep/nested/dir"}


async def test_command_changes_come_back_through_egress(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def mutate(files: dict[str, WorkspaceFile]) -> dict[str, WorkspaceFile]:
        files["generated.txt"] = WorkspaceFile(b"made by the command\n")
        files["a.txt"] = WorkspaceFile(b"rewritten\n")
        del files["gone.txt"]
        return files

    patch_docker(monkeypatch, FakeSpec(stdout=b"done\n", mutate=mutate))
    ws = await _ws({"a.txt": b"alpha\n", "gone.txt": b"bye\n"})
    outcome = await sbx.run_workspace(ws, "python build.py")
    assert outcome.result.error is None
    assert outcome.result.exit_code == 0
    assert outcome.result.stdout == "done\n"
    assert outcome.result.ingress_bytes and outcome.result.ingress_bytes > 0
    assert outcome.files is not None

    delta = sbx.compute_delta(ws, outcome.files)
    assert {e.path: e.change_kind for e in delta.entries} == {
        "generated.txt": "added",
        "a.txt": "modified",
        "gone.txt": "deleted",
    }


async def test_container_and_its_anonymous_volume_are_always_removed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = patch_docker(monkeypatch, FakeSpec())
    assert client is not None
    ws = await _ws({"a.txt": b"a\n"})
    await sbx.run_workspace(ws, "true")
    c = client.containers.container
    assert c is not None and c.removed and c.removed_volumes


async def test_oom_kill_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    patch_docker(monkeypatch, FakeSpec(exit_code=137, oom=True))
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "python -c 'x=[0]*10**9'")
    assert outcome.result.error == sbx.MEM_LIMIT == "mem_limit"


async def test_output_is_bounded_and_marked_truncated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    flood = b"x" * (sbx.OUTPUT_MAX_BYTES + 5000)
    patch_docker(monkeypatch, FakeSpec(stdout=flood))
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "yes")
    assert len(outcome.result.stdout) <= sbx.OUTPUT_MAX_BYTES
    assert outcome.result.output_truncated is True


# --- 3. credential boundary (config §1.7) -----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "sub/dir/.env",
        "certs/server.pem",
        "keys/private.key",
        "keys/id_rsa",
        ".git/config",
        "vendor/lib/.git/config",
    ],
)
def test_credential_shaped_paths_are_recognized(path: str) -> None:
    assert is_credential_path(path) is True


@pytest.mark.parametrize("path", ["app/main.py", "README.md", "src/config.py", "environments.md"])
def test_ordinary_paths_are_not_credential_shaped(path: str) -> None:
    assert is_credential_path(path) is False


def test_build_refuses_a_credential_shaped_member() -> None:
    """Defensive assertion: even if a caller forgets to strip, the bytes never leave here."""
    t = TarTransport(max_bytes=1_000_000)
    with pytest.raises(TransportError) as ei:
        t.build({".env": WorkspaceFile(CANARY.encode())}, set())
    assert ei.value.code == "credential_leak"


async def test_canary_never_enters_the_tar_or_the_delta(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The canary config §1.7 demands: a KEK-shaped secret in the project tree must not
    appear in the tar, and holding it back must NOT look like a deletion."""
    client = patch_docker(monkeypatch, FakeSpec(stdout=b"ok\n"))
    assert client is not None
    ws = await _ws(
        {
            ".env": f"KEK={CANARY}\n".encode(),
            "deploy/id_rsa": f"-----BEGIN-----\n{CANARY}\n".encode(),
            "app/main.py": b"print('hi')\n",
        }
    )
    assert ws.held_back == {".env", "deploy/id_rsa"}
    assert ws.sendable.keys() == {"app/main.py"}

    outcome = await sbx.run_workspace(ws, "python app/main.py")
    c = client.containers.container
    assert c is not None
    assert CANARY.encode() not in c.ingested_tar
    assert ".env" not in c.ingested
    assert "deploy/id_rsa" not in c.ingested

    assert outcome.files is not None
    delta = sbx.compute_delta(ws, outcome.files)
    # No change at all: the secret is untouched, and definitely not deleted.
    assert delta.entries == []
    assert delta.over_bounds is False


async def test_a_credential_written_by_the_command_is_dropped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def mutate(files: dict[str, WorkspaceFile]) -> dict[str, WorkspaceFile]:
        files[".env"] = WorkspaceFile(f"STOLEN={CANARY}\n".encode())
        return files

    patch_docker(monkeypatch, FakeSpec(mutate=mutate))
    ws = await _ws({"app/main.py": b"x\n"})
    outcome = await sbx.run_workspace(ws, "python app/main.py")
    assert outcome.files is not None
    assert ".env" not in outcome.files
    delta = sbx.compute_delta(ws, outcome.files)
    assert delta.entries == []


async def test_a_host_side_edit_to_a_held_back_path_still_persists() -> None:
    """Holding a path back from the *sandbox* must not silently discard a deliberate edit."""
    ws = await _ws({"app/main.py": b"x\n"})
    sbx.apply_edit(ws, sbx.ScratchEdit(path=".env", op="write", data=b"A=1\n"))
    assert ".env" in ws.held_back
    delta = sbx.compute_delta(ws, ws.files)
    assert {e.path: e.change_kind for e in delta.entries} == {".env": "added"}


# --- 4. untrusted egress ----------------------------------------------------


def _hostile_tar(build) -> bytes:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        build(tf)
    return buf.getvalue()


def _add(tf: tarfile.TarFile, name: str, data: bytes = b"x") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def test_egress_rejects_absolute_path() -> None:
    t = TarTransport(max_bytes=1_000_000)
    raw = _hostile_tar(lambda tf: _add(tf, "/etc/passwd"))
    with pytest.raises(TransportError) as ei:
        t.expand(raw)
    assert ei.value.code == "path_escape"


def test_egress_rejects_parent_traversal() -> None:
    t = TarTransport(max_bytes=1_000_000)
    raw = _hostile_tar(lambda tf: _add(tf, "work/../../evil.txt"))
    with pytest.raises(TransportError) as ei:
        t.expand(raw)
    assert ei.value.code == "path_escape"


def test_egress_rejects_hard_link() -> None:
    def build(tf: tarfile.TarFile) -> None:
        _add(tf, "work/a.txt")
        info = tarfile.TarInfo("work/link")
        info.type = tarfile.LNKTYPE
        info.linkname = "work/a.txt"
        tf.addfile(info)

    t = TarTransport(max_bytes=1_000_000)
    with pytest.raises(TransportError) as ei:
        t.expand(_hostile_tar(build))
    assert ei.value.code == "path_escape"


def test_egress_rejects_device_node() -> None:
    def build(tf: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("work/dev")
        info.type = tarfile.CHRTYPE
        info.devmajor, info.devminor = 1, 3
        tf.addfile(info)

    t = TarTransport(max_bytes=1_000_000)
    with pytest.raises(TransportError) as ei:
        t.expand(_hostile_tar(build))
    assert ei.value.code == "path_escape"


def test_egress_rejects_fifo() -> None:
    def build(tf: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("work/pipe")
        info.type = tarfile.FIFOTYPE
        tf.addfile(info)

    t = TarTransport(max_bytes=1_000_000)
    with pytest.raises(TransportError) as ei:
        t.expand(_hostile_tar(build))
    assert ei.value.code == "path_escape"


def test_egress_rejects_escaping_symlink_after_prefix_strip() -> None:
    """The prefix must be stripped BEFORE validation: with ``work/`` still attached,
    ``work/a -> ../evil`` resolves to ``evil`` (inside the archive) and would pass."""

    def build(tf: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("work/a")
        info.type = tarfile.SYMTYPE
        info.linkname = "../evil"
        tf.addfile(info)

    t = TarTransport(max_bytes=1_000_000)
    with pytest.raises(TransportError) as ei:
        t.expand(_hostile_tar(build))
    assert ei.value.code == "path_escape"


def test_egress_accepts_a_safe_relative_symlink() -> None:
    def build(tf: tarfile.TarFile) -> None:
        _add(tf, "work/src/a.txt", b"a\n")
        info = tarfile.TarInfo("work/src/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "a.txt"
        tf.addfile(info)

    t = TarTransport(max_bytes=1_000_000)
    files = t.expand(_hostile_tar(build))
    # Symlinks carry no persistable bytes; the regular file survives.
    assert set(files) == {"src/a.txt"}


def test_egress_is_bounded() -> None:
    t = TarTransport(max_bytes=32)
    raw = _hostile_tar(lambda tf: _add(tf, "work/big.bin", b"y" * 4096))
    with pytest.raises(TransportError) as ei:
        t.expand(raw)
    assert ei.value.code == "scratch_too_large"


def test_ingress_is_bounded() -> None:
    t = TarTransport(max_bytes=16)
    with pytest.raises(TransportError) as ei:
        t.build({"big.bin": WorkspaceFile(b"z" * 4096)}, set())
    assert ei.value.code == "scratch_too_large"


async def test_hostile_egress_ends_the_exec_with_path_escape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    raw = _hostile_tar(lambda tf: _add(tf, "work/../../evil.txt"))
    patch_docker(monkeypatch, FakeSpec(egress_tar=raw))
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "python evil.py")
    assert outcome.result.error == "path_escape"
    # Nothing came back ⇒ the caller falls back to the host-side copy; no partial tree.
    assert outcome.files is None


# --- named transport failures ------------------------------------------------


async def test_put_archive_failure_is_a_named_transport_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    patch_docker(monkeypatch, FakeSpec(put_error=APIError("stream broken C:/host/kek.pem")))
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "true")
    assert outcome.result.error == sbx.RUNTIME_TRANSPORT_FAILED
    assert outcome.files is None


async def test_get_archive_failure_is_a_named_transport_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import NotFound

    patch_docker(monkeypatch, FakeSpec(get_error=NotFound("no such path")))
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "true")
    assert outcome.result.error == sbx.RUNTIME_TRANSPORT_FAILED


async def test_start_failure_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    patch_docker(monkeypatch, FakeSpec(start_error=APIError("cannot start")))
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "true")
    assert outcome.result.error == sbx.RUNTIME_START_FAILED


async def test_wall_timeout_kills_the_container(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = patch_docker(monkeypatch, FakeSpec(wait_error=TimeoutError("read timed out")))
    assert client is not None
    ws = await _ws({"a.txt": b"a\n"})
    outcome = await sbx.run_workspace(ws, "sleep 999")
    assert outcome.result.timed_out is True
    c = client.containers.container
    assert c is not None and c.killed
