"""Workspace Projects W3 sandbox orchestration (ADR-040 + ADR-039).

Three layers:
* **mechanics** (no DB) — materialize an effective tree into a fresh disposable scratch,
  host-side edits, path-escape rejection, scratch-vs-base delta, change-set bounds, and the
  orphan sweep. The docker container path is gated by ``SANDBOX_KIND`` (browser-exercised).
* **failure classification** (no DB, fake docker client) — every container-path failure maps to
  its OWN named termination reason (events §2.11 ④); ``sandbox_unavailable`` no longer exists.
* **orchestration** (DB, in-memory object store) — one ``project_run`` boundary persists the
  scratch delta into the durable overlay fence-guarded; a missing dependency is an explicit
  named outcome; an over-bound delta persists nothing; every failing exit emits exactly one
  structured worker log line and one redacted observation.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.config import settings
from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import Tenant, User
from app.sandbox import project_sandbox as psbx
from app.sandbox.runner import RunResult
from app.services import project_sandbox as sbx_svc
from app.services import project_workcopy as wc_svc
from app.services import projects as projects_svc
from app.services.context import CallerContext


@pytest.fixture(autouse=True)
def _scratch_root(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "sandbox_scratch_root", str(tmp_path / "scratch"))


# --- mechanics (no DB) ------------------------------------------------------


async def _mem_reader(blobs: dict[bytes, bytes]):  # type: ignore[no-untyped-def]
    async def _r(h: bytes) -> bytes:
        return blobs[h]

    return _r


@pytest.mark.asyncio
async def test_materialize_edit_and_delta() -> None:
    import hashlib

    a, b = b"alpha\n", b"beta\n"
    ha, hb = hashlib.sha256(a).digest(), hashlib.sha256(b).digest()
    entries = [
        psbx.MaterializeEntry("src", "dir", None, 0, False, None),
        psbx.MaterializeEntry("src/a.txt", "file", ha, len(a), False, None),
        psbx.MaterializeEntry("b.txt", "file", hb, len(b), False, None),
    ]
    run_id = "run-mech"
    await psbx.materialize(run_id, entries, await _mem_reader({ha: a, hb: b}))

    # Scratch holds ONLY the project bytes — no credential/.env leaked in.
    root = psbx.scratch_dir_for(run_id)
    assert (root / "src" / "a.txt").read_bytes() == a
    names = {p.name for p in root.rglob("*") if p.is_file()}
    assert names == {"a.txt", "b.txt"}

    psbx.apply_edit(run_id, psbx.ScratchEdit(path="src/a.txt", op="write", data=b"ALPHA2\n"))
    psbx.apply_edit(run_id, psbx.ScratchEdit(path="c.txt", op="write", data=b"new\n"))
    psbx.apply_edit(run_id, psbx.ScratchEdit(path="b.txt", op="delete"))

    delta = psbx.compute_delta(run_id, {"src/a.txt": ha, "b.txt": hb})
    by_path = {e.path: e.change_kind for e in delta.entries}
    assert by_path == {"src/a.txt": "modified", "c.txt": "added", "b.txt": "deleted"}
    assert delta.over_bounds is False
    psbx.cleanup(run_id)
    assert not psbx.scratch_dir_for(run_id).exists()


@pytest.mark.asyncio
async def test_path_escape_rejected() -> None:
    await psbx.materialize("run-esc", [], await _mem_reader({}))
    with pytest.raises(psbx.ScratchError) as ei:
        psbx.apply_edit("run-esc", psbx.ScratchEdit(path="../evil", op="write", data=b"x"))
    assert ei.value.code == "path_escape"


@pytest.mark.asyncio
async def test_delta_bounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "working_copy_max_changed_files", 1)
    await psbx.materialize("run-bnd", [], await _mem_reader({}))
    psbx.apply_edit("run-bnd", psbx.ScratchEdit(path="a.txt", op="write", data=b"a"))
    psbx.apply_edit("run-bnd", psbx.ScratchEdit(path="b.txt", op="write", data=b"b"))
    delta = psbx.compute_delta("run-bnd", {})
    assert delta.over_bounds is True


def test_sweep_orphans() -> None:
    root = psbx.scratch_dir_for("keep").parent
    root.mkdir(parents=True, exist_ok=True)
    (root / "crash-1").mkdir()
    (root / "crash-2").mkdir()
    (root / "live").mkdir()
    removed = psbx.sweep_orphans(keep_run_ids={"live"})
    assert removed == 2
    assert (root / "live").exists()
    assert not (root / "crash-1").exists()


# --- failure classification (no DB, fake docker client) ---------------------
#
# B-8 regression: a start failure, an unreachable daemon, a missing image and a disabled
# sandbox used to collapse into ONE indistinguishable ``sandbox_unavailable`` with no log.
# Each must now surface its own contract-named reason (events §2.11 ④ / api §10.7).

# A host-shaped string planted in every injected docker exception: it may reach the operator
# log, but it must NEVER reach the model's observation (ADR-019 redaction).
_LEAKY = r"C:\host\private\kek.pem"

_CONTRACT_REASONS = {
    "done",
    "cancelled",
    "wall_timeout",
    "mem_limit",
    "pids_limit",
    "output_limit",
    "environment_missing_dependencies",
    "changeset_bounds",
    "path_escape",
    "fence_lost",
    "runtime_start_failed",
    "runtime_image_missing",
    "runtime_daemon_unreachable",
    "runtime_transport_failed",
    "sandbox_disabled",
}


class _FakeContainer:
    def __init__(self, *, logs_error: Exception | None = None) -> None:
        self._logs_error = logs_error
        self.removed = False

    def wait(self, timeout: float | None = None) -> dict[str, int]:
        return {"StatusCode": 0}

    def logs(self, stdout: bool = True, stderr: bool = False) -> bytes:
        if self._logs_error is not None:
            raise self._logs_error
        return b"hello\n" if stdout else b""

    def kill(self) -> None:
        pass

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self, *, run_error: Exception | None, container: _FakeContainer) -> None:
        self._run_error = run_error
        self._container = container
        self.mounts: object = None

    def run(self, image: str, **kwargs: object) -> _FakeContainer:
        self.mounts = kwargs.get("mounts")
        if self._run_error is not None:
            raise self._run_error
        return self._container


class _FakeDockerClient:
    def __init__(self, *, run_error: Exception | None, logs_error: Exception | None) -> None:
        self.containers = _FakeContainers(
            run_error=run_error, container=_FakeContainer(logs_error=logs_error)
        )


def _patch_docker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    from_env_error: Exception | None = None,
    run_error: Exception | None = None,
    logs_error: Exception | None = None,
) -> None:
    import docker

    def _from_env(*args: object, **kwargs: object) -> _FakeDockerClient:
        if from_env_error is not None:
            raise from_env_error
        return _FakeDockerClient(run_error=run_error, logs_error=logs_error)

    monkeypatch.setattr(settings, "sandbox_kind", "docker")
    monkeypatch.setattr(docker, "from_env", _from_env)


@pytest.mark.asyncio
async def test_disabled_sandbox_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "sandbox_kind", "disabled")
    res = await psbx.run_in_scratch("run-disabled", "pytest -q")
    assert res.error == psbx.SANDBOX_DISABLED == "sandbox_disabled"


@pytest.mark.asyncio
async def test_daemon_unreachable_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import DockerException

    _patch_docker(monkeypatch, from_env_error=DockerException(f"cannot connect {_LEAKY}"))
    res = await psbx.run_in_scratch("run-daemon", "pytest -q")
    assert res.error == psbx.RUNTIME_DAEMON_UNREACHABLE == "runtime_daemon_unreachable"
    # The raw detail is kept separate from the named reason, for the operator log only.
    assert res.error_detail is not None and _LEAKY in res.error_detail


@pytest.mark.asyncio
async def test_image_missing_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import ImageNotFound

    _patch_docker(monkeypatch, run_error=ImageNotFound(f"no such image {_LEAKY}"))
    res = await psbx.run_in_scratch("run-image", "pytest -q")
    assert res.error == psbx.RUNTIME_IMAGE_MISSING == "runtime_image_missing"


@pytest.mark.asyncio
async def test_container_start_failure_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    _patch_docker(monkeypatch, run_error=APIError(f"invalid mount source {_LEAKY}"))
    res = await psbx.run_in_scratch("run-start", "pytest -q")
    assert res.error == psbx.RUNTIME_START_FAILED == "runtime_start_failed"


@pytest.mark.asyncio
async def test_output_retrieval_failure_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    _patch_docker(monkeypatch, logs_error=APIError(f"stream broken {_LEAKY}"))
    res = await psbx.run_in_scratch("run-transport", "pytest -q")
    assert res.error == psbx.RUNTIME_TRANSPORT_FAILED == "runtime_transport_failed"


@pytest.mark.asyncio
async def test_unexpected_failure_carries_the_error_class(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_docker(monkeypatch, run_error=ValueError(f"boom {_LEAKY}"))
    res = await psbx.run_in_scratch("run-weird", "pytest -q")
    assert res.error == "error:ValueError"


@pytest.mark.asyncio
async def test_successful_run_reports_no_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_docker(monkeypatch)
    res = await psbx.run_in_scratch("run-ok", "echo hello")
    assert res.error is None
    assert res.exit_code == 0
    assert res.stdout == "hello\n"


def test_runtime_failure_reasons_are_distinct_and_in_the_contract() -> None:
    reasons = [
        psbx.SANDBOX_DISABLED,
        psbx.RUNTIME_DAEMON_UNREACHABLE,
        psbx.RUNTIME_IMAGE_MISSING,
        psbx.RUNTIME_START_FAILED,
        psbx.RUNTIME_TRANSPORT_FAILED,
    ]
    assert len(set(reasons)) == len(reasons)
    assert set(reasons) <= _CONTRACT_REASONS
    # The blanket collapse is gone for good.
    assert "sandbox_unavailable" not in set(reasons) | set(sbx_svc.FAILURE_NOTES)


@pytest.mark.parametrize(
    "reason",
    [
        "sandbox_disabled",
        "runtime_daemon_unreachable",
        "runtime_image_missing",
        "runtime_start_failed",
        "runtime_transport_failed",
        "wall_timeout",
        "environment_missing_dependencies",
        "changeset_bounds",
        "path_escape",
        "fence_lost",
    ],
)
def test_every_named_failure_has_a_redacted_model_note(reason: str) -> None:
    note = sbx_svc.failure_note(reason)
    assert note and reason in note
    # A model-facing observation never carries raw host paths / exception text.
    assert _LEAKY not in note


def test_unknown_failure_still_gets_a_note() -> None:
    note = sbx_svc.failure_note("error:RuntimeError")
    assert "error:RuntimeError" in note


# --- orchestration (DB) -----------------------------------------------------


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


async def _bound_session(s, ctx, project) -> SessionModel:  # type: ignore[no-untyped-def]
    sid = uuid.uuid4()
    session = SessionModel(
        tenant_id=ctx.tenant_id,
        id=sid,
        user_id=ctx.user_id,
        umo_key=f"web:chat:{sid}",
        channel="web",
        channel_installation_id="local",
        scope_type="chat",
        external_scope_id=str(sid),
        status="open",
        project_id=project.id,
        admitted_seq=1,
    )
    s.add(session)
    await s.flush()
    return session


async def _open_wc(s, ctx):  # type: ignore[no-untyped-def]
    project = await projects_svc.create_project(s, ctx, name="P", template_id="python-basic")
    session = await _bound_session(s, ctx, project)
    wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)
    return project, wc


@pytest.mark.asyncio
async def test_run_sandbox_persists_edit_delta() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            req = sbx_svc.SandboxRequest(
                edits=[
                    psbx.ScratchEdit(path="added.txt", op="write", data=b"new file\n"),
                    psbx.ScratchEdit(path="README.md", op="write", data=b"# changed\n"),
                    psbx.ScratchEdit(path="requirements.txt", op="delete"),
                ],
                command=None,
            )
            out = await sbx_svc.run_sandbox(s, ctx, wc, run_id=uuid.uuid4(), request=req)
            # Edits-only boundary: no command ran, so there is no exec run and the named
            # exit lives on the runtime session itself.
            assert out.exec_run is None
            assert out.runtime_session.state == "closed"
            assert out.runtime_session.closed_at is not None
            assert out.termination_reason == "done"
            # The overlay reflects the scratch delta; scratch is torn down.
            eff = await wc_svc.effective_tree(s, ctx, wc)
            assert eff["added.txt"].content_hash is not None
            assert "requirements.txt" not in eff
            assert out.runtime_session.container_ref is None
            assert not psbx.scratch_dir_for(str(out.runtime_session.id)).exists()
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sandbox_missing_dependency_still_persists_edits(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "sandbox_kind", "docker")

    async def _fake(scratch_dir: str, command: str) -> RunResult:
        return RunResult("", "sh: ruff: not found", 127, False)

    monkeypatch.setattr(psbx, "_execute_in_scratch", _fake)
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            req = sbx_svc.SandboxRequest(
                edits=[psbx.ScratchEdit(path="edited.txt", op="write", data=b"x\n")],
                command="ruff check .",
            )
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="app.services.project_sandbox"):
                out = await sbx_svc.run_sandbox(s, ctx, wc, run_id=uuid.uuid4(), request=req)
            # Missing dependency is an EXPLICIT named outcome; the edit is still persisted.
            assert out.exec_run is not None
            assert out.exec_run.termination_reason == "environment_missing_dependencies"
            assert out.exec_run.exit_code == 127
            assert out.exec_run.state == "persisted"
            assert out.exec_run.persisted_boundary_at is not None
            assert out.runtime_session.state == "closed"
            assert out.failure_note is not None
            assert "environment_missing_dependencies" in out.failure_note
            assert len([r for r in caplog.records if r.name == "app.services.project_sandbox"]) == 1
            eff = await wc_svc.effective_tree(s, ctx, wc)
            assert eff["edited.txt"].content_hash is not None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sandbox_over_bounds_persists_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "working_copy_max_changed_files", 1)
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            req = sbx_svc.SandboxRequest(
                edits=[
                    psbx.ScratchEdit(path="one.txt", op="write", data=b"1\n"),
                    psbx.ScratchEdit(path="two.txt", op="write", data=b"2\n"),
                ],
            )
            out = await sbx_svc.run_sandbox(s, ctx, wc, run_id=uuid.uuid4(), request=req)
            assert out.runtime_session.state == "failed"
            assert out.exec_run is None
            assert out.termination_reason == "changeset_bounds"
            # No overlay persisted (never a silent partial).
            assert wc.overlay_entry_count == 0
            # The failure is still an observation, not a crash.
            assert out.failure_note is not None
            assert "changeset_bounds" in out.failure_note
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sandbox_names_each_failure_and_logs_exactly_once(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    """B-8 regression: a disabled sandbox, an unreachable daemon and a missing image produce
    THREE different ``termination_reason``s, three structured worker log lines, and three
    redacted model observations — never one blanket ``sandbox_unavailable`` with no log."""
    if not await ping_db():
        pytest.skip("database not reachable")
    from docker.errors import APIError, DockerException, ImageNotFound

    scenarios: list[tuple[str, Exception | None, str, str]] = [
        ("disabled", None, "sandbox_disabled", "disabled.txt"),
        (
            "from_env",
            DockerException(f"cannot connect {_LEAKY}"),
            "runtime_daemon_unreachable",
            "daemon.txt",
        ),
        ("run", ImageNotFound(f"no such image {_LEAKY}"), "runtime_image_missing", "image.txt"),
        ("run", APIError(f"invalid mount source {_LEAKY}"), "runtime_start_failed", "start.txt"),
    ]

    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            seen: list[str] = []
            for where, exc, expected, path in scenarios:
                if exc is None:
                    monkeypatch.setattr(settings, "sandbox_kind", "disabled")
                elif where == "from_env":
                    _patch_docker(monkeypatch, from_env_error=exc)
                else:
                    _patch_docker(monkeypatch, run_error=exc)

                caplog.clear()
                with caplog.at_level(logging.WARNING, logger="app.services.project_sandbox"):
                    out = await sbx_svc.run_sandbox(
                        s,
                        ctx,
                        wc,
                        run_id=uuid.uuid4(),
                        request=sbx_svc.SandboxRequest(
                            edits=[psbx.ScratchEdit(path=path, op="write", data=b"x\n")],
                            command="pytest -q",
                        ),
                    )

                assert out.termination_reason == expected
                seen.append(expected)

                # Exactly ONE structured worker log line, naming the same reason.
                records = [r for r in caplog.records if r.name == "app.services.project_sandbox"]
                assert len(records) == 1
                assert records[0].termination_reason == expected  # type: ignore[attr-defined]
                assert records[0].runtime_session_id == str(out.runtime_session.id)  # type: ignore[attr-defined]

                # The redacted model observation names the reason and leaks no host detail.
                assert out.failure_note is not None
                assert expected in out.failure_note
                assert _LEAKY not in out.failure_note
                if exc is not None:
                    # The raw detail survives for the operator, in the log line only.
                    assert _LEAKY in (records[0].sandbox_error_detail or "")  # type: ignore[attr-defined]

                # Error-is-observation: the host-side edit is still durably persisted.
                eff = await wc_svc.effective_tree(s, ctx, wc)
                assert eff[path].content_hash is not None

            assert len(set(seen)) == len(scenarios)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sandbox_success_logs_nothing_and_has_no_note(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    _patch_docker(monkeypatch)
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="app.services.project_sandbox"):
                out = await sbx_svc.run_sandbox(
                    s,
                    ctx,
                    wc,
                    run_id=uuid.uuid4(),
                    request=sbx_svc.SandboxRequest(command="echo hello"),
                )
            assert out.termination_reason == "done"
            assert out.exec_run is not None
            assert out.exec_run.state == "persisted"
            assert out.exec_run.seq == 1
            assert out.exec_run.command_preview == "echo hello"
            assert out.runtime_session.state == "closed"
            assert out.failure_note is None
            assert [r for r in caplog.records if r.name == "app.services.project_sandbox"] == []
        finally:
            await s.rollback()
