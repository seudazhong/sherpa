"""The real-container lane (Phase TR P3.7) — ``uv run pytest -m docker``.

Excluded from the default run because CI has no Docker daemon. **This is the only lane that
can catch a broken container transport.** Backlog B-8 survived 297 green tests precisely
because every sandbox test substituted a fake executor, so the bind mount that could never
work was never executed. The same blind spot burned Phase TR P2.2 at the wire-format layer
(mock provider, dotted tool names, all green, zero working tool calls). The standing lesson
is the same in both cases: some contracts are only verifiable against the real thing.

Every test here starts a real container from the real ``sherpa-sandbox-runner`` image with
the real ADR-025 hardening flags, and asserts on real exit codes and real bytes.

Prerequisites (both are skipped, not failed, when missing):
    docker build -t sherpa-sandbox-runner:dev sandbox-runner
"""

from __future__ import annotations

import asyncio
import hashlib
import time

import pytest

from app.config import settings
from app.sandbox import runtime as sbx
from app.sandbox.transport import WorkspaceFile

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def _real_sandbox(docker_runner_image: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_kind", "docker")
    monkeypatch.setattr(settings, "sandbox_image", docker_runner_image)


async def _ws(files: dict[str, bytes]) -> sbx.Workspace:
    blobs = {hashlib.sha256(d).digest(): d for d in files.values()}

    async def _read(h: bytes) -> bytes:
        return blobs[h]

    entries = [
        sbx.MaterializeEntry(
            path=p,
            entry_kind="file",
            content_hash=hashlib.sha256(d).digest(),
            size_bytes=len(d),
            executable=False,
            symlink_target=None,
        )
        for p, d in files.items()
    ]
    return await sbx.materialize(entries, _read)


async def test_real_container_returns_a_real_exit_code_and_stdout() -> None:
    ws = await _ws({"README.md": b"# hi\n"})
    out = await sbx.run_workspace(ws, "python -c \"print('hello from the sandbox')\"")
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code == 0
    assert "hello from the sandbox" in out.result.stdout


async def test_real_container_reports_a_real_nonzero_exit_code() -> None:
    ws = await _ws({"README.md": b"# hi\n"})
    out = await sbx.run_workspace(ws, "python -c 'import sys; sys.exit(3)'")
    assert out.result.error is None
    assert out.result.exit_code == 3


async def test_the_ingested_workspace_is_actually_there_and_writable() -> None:
    ws = await _ws({"src/app.py": b"VALUE = 41\n"})
    out = await sbx.run_workspace(
        ws,
        "cat src/app.py && python -c \"open('src/generated.txt','w').write('made it')\" "
        "&& mkdir -p src/sub && echo nested > src/sub/n.txt",
    )
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code == 0, out.result.stderr
    assert "VALUE = 41" in out.result.stdout
    assert out.files is not None
    assert out.files["src/generated.txt"].data == b"made it"
    assert out.files["src/sub/n.txt"].data == b"nested\n"

    delta = sbx.compute_delta(ws, out.files)
    assert {e.path: e.change_kind for e in delta.entries} == {
        "src/generated.txt": "added",
        "src/sub/n.txt": "added",
    }


async def test_pytest_runs_for_real_and_the_failure_round_trips() -> None:
    """The edit/test loop P4 will drive: a failing test really fails, a fixed one passes."""
    test_src = b"from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    ws = await _ws(
        {
            "calc.py": b"def add(a, b):\n    return a - b\n",
            "test_calc.py": test_src,
        }
    )
    out = await sbx.run_workspace(ws, "pytest -q")
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code != 0
    assert "1 failed" in out.result.stdout

    sbx.apply_edit(
        ws, sbx.ScratchEdit(path="calc.py", op="write", data=b"def add(a, b):\n    return a + b\n")
    )
    out2 = await sbx.run_workspace(ws, "pytest -q")
    assert out2.result.exit_code == 0
    assert "1 passed" in out2.result.stdout
    # The cache settings in the runner image keep .pytest_cache out of the change set.
    assert out2.files is not None
    assert not any(p.startswith(".pytest_cache") for p in out2.files)
    assert not any("__pycache__" in p for p in out2.files)


async def test_ruff_runs_for_real() -> None:
    ws = await _ws({"bad.py": b"import os\n"})
    out = await sbx.run_workspace(ws, "ruff --version && ruff check --select F401 .")
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code != 0
    assert "F401" in out.result.stdout


async def test_the_container_has_no_network() -> None:
    """ADR-025: network_disabled. No egress, ever."""
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(
        ws,
        "python -c \"import socket;socket.create_connection(('1.1.1.1',53),timeout=3)\" "
        "&& echo REACHED || echo BLOCKED",
    )
    assert out.result.error is None, out.result.error_detail
    assert "BLOCKED" in out.result.stdout
    assert "REACHED" not in out.result.stdout


async def test_the_container_is_non_root_with_a_read_only_rootfs() -> None:
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(
        ws, "id -u; (touch /etc/should-fail && echo ROOTFS_WRITABLE) || echo ROOTFS_READONLY"
    )
    assert out.result.error is None, out.result.error_detail
    assert out.result.stdout.splitlines()[0] == "10001"
    assert "ROOTFS_READONLY" in out.result.stdout


async def test_no_credential_or_docker_socket_is_visible_inside() -> None:
    """The sandbox must not be able to see the orchestrator's secrets or its socket."""
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(
        ws,
        "env | grep -Ei 'kek|secret|password|api_key|token' || echo NO_SECRET_ENV; "
        "test -S /var/run/docker.sock && echo SOCKET_VISIBLE || echo NO_SOCKET",
    )
    assert out.result.error is None, out.result.error_detail
    assert "NO_SECRET_ENV" in out.result.stdout
    assert "NO_SOCKET" in out.result.stdout
    assert "SOCKET_VISIBLE" not in out.result.stdout


async def test_a_missing_tool_is_reported_as_a_missing_dependency_not_a_download() -> None:
    """The image deliberately has no git and no network: exit 127, which the orchestrator
    maps to environment_missing_dependencies."""
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "git status")
    assert out.result.error is None
    assert out.result.exit_code == 127


async def test_a_missing_image_is_its_own_named_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A digest-pinned image that is simply not built must say so. "Not built yet" and "not
    allowed" are different operator problems and must not collapse into one name — so the
    reference here is deliberately well-formed and absent, not a tag."""
    monkeypatch.setattr(settings, "sandbox_image", "sha256:" + "1e" * 32)
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_MISSING


async def test_wall_timeout_kills_a_real_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_run_timeout_seconds", 3)
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "sleep 999")
    assert out.result.timed_out is True


async def test_a_real_oom_is_named_mem_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker's OOMKilled flag, read back after wait — an exit 137 alone would not say why."""
    monkeypatch.setattr(settings, "sandbox_mem_mb", 128)
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, 'python -c "b=bytearray(2*1024**3)"')
    assert out.result.error == sbx.MEM_LIMIT
    assert out.result.exit_code == 137


async def test_a_flooding_command_is_bounded_not_fatal() -> None:
    """Output is capped and flagged; the command itself still settles normally. The typed
    spill reference (and an `output_limit` termination reason) is api §7.2 debt, tracked in
    Phase TR P2.8 — this test records the behaviour that ships today, not the target."""
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "yes | head -c 3000000")
    assert out.result.error is None
    assert out.result.exit_code == 0
    assert out.result.output_truncated is True
    assert len(out.result.stdout) <= sbx.OUTPUT_MAX_BYTES


async def test_a_credential_shaped_file_never_reaches_the_real_container() -> None:
    canary = "sherpa-kek-canary-MDEyMzQ1Njc4OWFiY2RlZg=="
    ws = await _ws({".env": f"KEK={canary}\n".encode(), "app.py": b"print(1)\n"})
    out = await sbx.run_workspace(ws, "ls -a; cat .env 2>/dev/null || echo NO_ENV_FILE")
    assert out.result.error is None, out.result.error_detail
    assert "NO_ENV_FILE" in out.result.stdout
    assert canary not in out.result.stdout
    # And holding it back is not a deletion.
    assert out.files is not None
    delta = sbx.compute_delta(ws, out.files)
    assert delta.entries == []


async def test_the_container_and_its_volume_are_removed() -> None:
    import docker

    client = docker.from_env()
    before = {c.id for c in client.containers.list(all=True)}
    volumes_before = {v.id for v in client.volumes.list()}
    ws = await _ws({"a.txt": b"a\n"})
    await sbx.run_workspace(ws, "true")
    after = {c.id for c in client.containers.list(all=True)}
    assert after - before == set()
    assert {v.id for v in client.volumes.list()} - volumes_before == set()


async def test_the_orphan_sweep_only_touches_our_labelled_containers() -> None:
    """A crashed worker leaves a *stale* owned container; the sweep removes it and nothing
    else. Staleness matters: the age rule is what stops the sweep racing a live run."""
    import docker

    client = docker.from_env()
    stale = client.containers.create(
        settings.sandbox_image,
        command=["/bin/sh", "-lc", "true"],
        labels={
            sbx.RUNTIME_LABEL: "1",
            sbx.OWNER_LABEL: sbx.deployment_owner_id(),
            sbx.SESSION_LABEL: "crashed-worker",
            sbx.STARTED_LABEL: f"{time.time() - 86400:.3f}",  # a day old
        },
    )
    unrelated = client.containers.create(settings.sandbox_image, command=["/bin/sh", "-lc", "true"])
    try:
        removed = sbx.sweep_orphan_containers()
        assert removed >= 1
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(stale.id)
        assert client.containers.get(unrelated.id) is not None
    finally:
        for c in (stale, unrelated):
            try:
                c.remove(force=True, v=True)
            except Exception:  # noqa: BLE001
                pass


async def test_the_sweep_never_removes_another_deployments_container() -> None:
    """The confirmed production race, in the shape that actually happened.

    The dev worker's maintenance cron swept **every** ``sherpa.runtime`` container, so a
    concurrently running test lane had its container deleted mid-run and the run died with
    ``409 container is dead or marked for removal``. Ownership scoping is what fixes it: a
    container belonging to a different deployment id must be invisible to our sweeper —
    even when it is stale, and even when it is stopped."""
    import docker

    client = docker.from_env()
    foreign = client.containers.create(
        settings.sandbox_image,
        command=["/bin/sh", "-lc", "true"],
        labels={
            sbx.RUNTIME_LABEL: "1",
            sbx.OWNER_LABEL: "some-other-deployment",
            sbx.SESSION_LABEL: "theirs",
            sbx.STARTED_LABEL: f"{time.time() - 86400:.3f}",
        },
    )
    try:
        sbx.sweep_orphan_containers()
        assert client.containers.get(foreign.id) is not None, (
            "the sweeper deleted another deployment's container — the confirmed 409 race"
        )
    finally:
        try:
            foreign.remove(force=True, v=True)
        except Exception:  # noqa: BLE001
            pass


async def test_a_live_run_survives_a_concurrent_sweep() -> None:
    """The race, end to end, with the sweeper firing *during* an actual execution.

    A maintenance tick is triggered repeatedly while a real container is mid-run. The run
    must complete normally — no 409, no lost container. Before the fix this deleted the
    container out from under the run."""
    import threading

    stop = threading.Event()
    sweeps: list[int] = []
    errors: list[BaseException] = []

    def sweeper() -> None:
        while not stop.is_set():
            try:
                sweeps.append(sbx.sweep_orphan_containers())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            stop.wait(0.05)

    thread = threading.Thread(target=sweeper, daemon=True)
    thread.start()
    try:
        ws = await _ws({"a.txt": b"a\n"})
        # Long enough that many sweeps land inside the run, including during tar ingress.
        out = await sbx.run_workspace(ws, "sleep 3; echo survived")
    finally:
        stop.set()
        thread.join(timeout=10)

    assert not errors, f"sweeper raised: {errors[:1]}"
    assert sweeps, "the sweeper never ran; the test proved nothing"
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code == 0
    assert "survived" in out.result.stdout
    assert out.files is not None


async def test_an_in_flight_container_is_never_reclaimed_even_when_stale() -> None:
    """Belt and braces: the in-process registry protects a container the age rule would
    otherwise release, so a run that somehow outlives the threshold is still not deleted
    by its own process."""
    import docker

    client = docker.from_env()
    c = client.containers.create(
        settings.sandbox_image,
        command=["/bin/sh", "-lc", "true"],
        labels={
            sbx.RUNTIME_LABEL: "1",
            sbx.OWNER_LABEL: sbx.deployment_owner_id(),
            sbx.SESSION_LABEL: "held",
            sbx.STARTED_LABEL: f"{time.time() - 86400:.3f}",
        },
    )
    try:
        sbx._IN_FLIGHT.add(c.id)
        try:
            sbx.sweep_orphan_containers()
            assert client.containers.get(c.id) is not None
        finally:
            sbx._IN_FLIGHT.discard(c.id)
        # Once released, the same stale container IS reclaimed.
        sbx.sweep_orphan_containers()
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(c.id)
    finally:
        try:
            c.remove(force=True, v=True)
        except Exception:  # noqa: BLE001
            pass


async def test_a_running_orphan_is_eventually_reclaimed() -> None:
    """Recovery must not stop at stopped containers: a crashed worker can leave one
    *executing*. It is reclaimed once past the age threshold — eventual by design, so it
    can never race a live run."""
    import docker

    client = docker.from_env()
    running = client.containers.run(
        settings.sandbox_image,
        command=["/bin/sh", "-lc", "sleep 600"],
        detach=True,
        labels={
            sbx.RUNTIME_LABEL: "1",
            sbx.OWNER_LABEL: sbx.deployment_owner_id(),
            sbx.SESSION_LABEL: "crashed-mid-run",
            sbx.STARTED_LABEL: f"{time.time() - 86400:.3f}",
        },
    )
    try:
        running.reload()
        assert running.status == "running"
        removed = sbx.sweep_orphan_containers()
        assert removed >= 1
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(running.id)
    finally:
        try:
            running.remove(force=True, v=True)
        except Exception:  # noqa: BLE001
            pass


async def test_a_recent_owned_container_is_spared() -> None:
    """The other half of the age rule: a young container may still belong to a live run in
    another process of this deployment (uploading a workspace, or having its logs and egress
    tar read after exit), so it is not reclaimed yet."""
    import docker

    client = docker.from_env()
    fresh = client.containers.create(
        settings.sandbox_image,
        command=["/bin/sh", "-lc", "true"],
        labels={
            sbx.RUNTIME_LABEL: "1",
            sbx.OWNER_LABEL: sbx.deployment_owner_id(),
            sbx.SESSION_LABEL: "just-created",
            sbx.STARTED_LABEL: f"{time.time():.3f}",
        },
    )
    try:
        sbx.sweep_orphan_containers()
        assert client.containers.get(fresh.id) is not None
    finally:
        try:
            fresh.remove(force=True, v=True)
        except Exception:  # noqa: BLE001
            pass


async def test_a_real_run_labels_its_container_with_owner_and_session() -> None:
    """The labels the sweeper depends on must actually be written by the real path."""
    import docker

    client = docker.from_env()
    seen: dict[str, str] = {}

    ws = await _ws({"a.txt": b"a\n"})

    async def capture() -> None:
        # Poll while the run is in flight and record the labels docker actually stored.
        for _ in range(200):
            for c in client.containers.list(
                all=True, filters={"label": f"{sbx.OWNER_LABEL}={sbx.deployment_owner_id()}"}
            ):
                if c.id in sbx._IN_FLIGHT:
                    seen.update(c.labels or {})
                    return
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(capture())
    out = await sbx.run_workspace(ws, "sleep 2; echo ok", session_label="session-xyz")
    await watcher

    assert out.result.error is None, out.result.error_detail
    assert seen.get(sbx.OWNER_LABEL) == sbx.deployment_owner_id()
    assert seen.get(sbx.SESSION_LABEL) == "session-xyz"
    assert float(seen[sbx.STARTED_LABEL]) > 0


async def test_a_workspace_file_keeps_its_executable_bit_in_a_real_container() -> None:
    ws = await _ws({"run.sh": b"#!/bin/sh\necho ran\n"})
    ws.files["run.sh"] = WorkspaceFile(data=ws.files["run.sh"].data, executable=True)
    out = await sbx.run_workspace(ws, "./run.sh")
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code == 0
    assert "ran" in out.result.stdout


async def test_a_real_chmod_plus_x_is_detected_without_a_content_change() -> None:
    """Blocker 3 against a real container: ``chmod +x`` alone, bytes untouched.

    The baseline used to store only a content hash, so this produced an **empty delta** and
    the mode change was silently dropped on the way to the change set."""
    ws = await _ws({"run.sh": b"#!/bin/sh\necho hi\n"})
    assert ws.files["run.sh"].executable is False
    out = await sbx.run_workspace(ws, "chmod +x run.sh && ls -l run.sh")
    assert out.result.error is None, out.result.error_detail
    assert out.result.exit_code == 0
    assert out.files is not None
    assert out.files["run.sh"].executable is True
    assert out.files["run.sh"].data == ws.files["run.sh"].data  # content really is identical

    delta = sbx.compute_delta(ws, out.files)
    assert {e.path: e.change_kind for e in delta.entries} == {"run.sh": "modified"}
    assert delta.entries[0].executable is True


async def test_a_real_chmod_minus_x_is_detected_without_a_content_change() -> None:
    ws = await _ws({"run.sh": b"#!/bin/sh\necho hi\n"})
    ws.files["run.sh"] = WorkspaceFile(data=ws.files["run.sh"].data, executable=True)
    ws.base_manifest["run.sh"] = sbx.BaselineEntry(
        content_hash=ws.base_manifest["run.sh"].content_hash, executable=True
    )
    out = await sbx.run_workspace(ws, "chmod -x run.sh && ls -l run.sh")
    assert out.result.error is None, out.result.error_detail
    assert out.files is not None
    assert out.files["run.sh"].executable is False
    delta = sbx.compute_delta(ws, out.files)
    assert {e.path: e.change_kind for e in delta.entries} == {"run.sh": "modified"}
    assert delta.entries[0].executable is False


async def test_a_real_untouched_workspace_produces_no_delta() -> None:
    """The other half: comparing the mode must not invent phantom churn on a real round trip."""
    ws = await _ws({"a.txt": b"a\n", "run.sh": b"#!/bin/sh\n"})
    out = await sbx.run_workspace(ws, "true")
    assert out.result.error is None, out.result.error_detail
    assert out.files is not None
    assert sbx.compute_delta(ws, out.files).entries == []


async def test_a_mutable_tag_is_refused_against_a_real_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocker 2 against a real daemon: the tag exists and is pullable locally, and must
    still be refused, because a tag can be re-pointed at different bytes after review."""
    monkeypatch.setattr(settings, "sandbox_image", "sherpa-sandbox-runner:dev")
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_UNTRUSTED


async def test_a_real_foreign_image_is_refused_even_when_digest_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real, immutable digest for an image that is not our runner must not run."""
    import docker
    from docker.errors import ImageNotFound

    client = docker.from_env()
    try:
        foreign = client.images.get("python:3.11-slim")
    except ImageNotFound:
        pytest.skip("python:3.11-slim not present locally")
    monkeypatch.setattr(settings, "sandbox_image", str(foreign.id))
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_UNTRUSTED


async def test_the_real_runner_advertises_the_identity_labels_we_check(
    docker_runner_image: str,
) -> None:
    """If the image ever stops carrying these, every sandbox run fails closed — so the
    contract between the Dockerfile and `verify_runner_image` is asserted explicitly."""
    import docker

    client = docker.from_env()
    labels = client.images.get(docker_runner_image).labels
    assert labels.get(sbx.RUNNER_TITLE_LABEL) == sbx.RUNNER_IMAGE_TITLE
    assert labels.get(sbx.RUNNER_CAPABILITIES_LABEL)


async def test_a_real_read_timeout_is_reported_as_a_timeout_not_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocker 4 against a real daemon: a genuine wall-clock overrun must set ``timed_out``
    and must NOT be reported as a daemon/transport failure."""
    monkeypatch.setattr(settings, "sandbox_run_timeout_seconds", 3)
    ws = await _ws({"a.txt": b"a\n"})
    out = await sbx.run_workspace(ws, "sleep 999")
    assert out.result.timed_out is True
    assert out.result.error is None
