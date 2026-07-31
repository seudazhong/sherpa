"""Workspace Projects W3 sandbox orchestration (ADR-040 + ADR-039 + ADR-047).

Three layers:
* **mechanics** (no DB, no docker) — materialize an effective tree into a fresh in-memory
  disposable copy, host-side edits, path-escape rejection, delta vs base, change-set bounds.
  Under tar transport (Phase TR P3) there is **no host scratch directory** any more.
* **failure classification** (no DB, fake docker client) — every container-path failure maps to
  its OWN named termination reason (events §2.11 ④); ``sandbox_unavailable`` no longer exists.
* **orchestration** (DB, in-memory object store) — one ``project_run`` boundary persists the
  delta into the durable overlay fence-guarded; a missing dependency is an explicit
  named outcome; an over-bound delta persists nothing; every failing exit emits exactly one
  structured worker log line and one redacted observation.

The tar transport itself (round trip, credential canary, hostile egress) lives in
``tests/test_runtime_transport.py``; the real-container lane lives in
``tests/test_runtime_docker.py`` (``uv run pytest -m docker``).
"""

from __future__ import annotations

import hashlib
import logging
import uuid

import pytest

from app.config import settings
from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import StorageBlob, Tenant, User
from app.sandbox import runtime as sbx
from app.services import project_changes as changes_svc
from app.services import project_sandbox as sbx_svc
from app.services import project_workcopy as wc_svc
from app.services import projects as projects_svc
from app.services.context import CallerContext
from tests.fake_docker import FakeSpec, patch_docker

# --- mechanics (no DB, no docker) -------------------------------------------


async def _mem_reader(blobs: dict[bytes, bytes]):  # type: ignore[no-untyped-def]
    async def _r(h: bytes) -> bytes:
        return blobs[h]

    return _r


@pytest.mark.asyncio
async def test_materialize_edit_and_delta() -> None:
    a, b = b"alpha\n", b"beta\n"
    ha, hb = hashlib.sha256(a).digest(), hashlib.sha256(b).digest()
    entries = [
        sbx.MaterializeEntry("src", "dir", None, 0, False, None),
        sbx.MaterializeEntry("src/a.txt", "file", ha, len(a), False, None),
        sbx.MaterializeEntry("b.txt", "file", hb, len(b), False, None),
    ]
    ws = await sbx.materialize(entries, await _mem_reader({ha: a, hb: b}))

    # The copy holds ONLY the project bytes — no credential/.env leaked in.
    assert set(ws.files) == {"src/a.txt", "b.txt"}
    assert ws.files["src/a.txt"].data == a
    assert ws.dirs == {"src"}
    assert ws.held_back == set()

    sbx.apply_edit(ws, sbx.ScratchEdit(path="src/a.txt", op="write", data=b"ALPHA2\n"))
    sbx.apply_edit(ws, sbx.ScratchEdit(path="c.txt", op="write", data=b"new\n"))
    sbx.apply_edit(ws, sbx.ScratchEdit(path="b.txt", op="delete"))

    delta = sbx.compute_delta(ws, ws.files)
    by_path = {e.path: e.change_kind for e in delta.entries}
    assert by_path == {"src/a.txt": "modified", "c.txt": "added", "b.txt": "deleted"}
    assert delta.over_bounds is False


@pytest.mark.asyncio
async def test_path_escape_rejected() -> None:
    ws = await sbx.materialize([], await _mem_reader({}))
    with pytest.raises(sbx.ScratchError) as ei:
        sbx.apply_edit(ws, sbx.ScratchEdit(path="../evil", op="write", data=b"x"))
    assert ei.value.code == "path_escape"


@pytest.mark.asyncio
async def test_absolute_and_nul_paths_are_rejected() -> None:
    ws = await sbx.materialize([], await _mem_reader({}))
    for bad in ("/etc/passwd", "a\x00b", "  ", ".."):
        with pytest.raises(sbx.ScratchError) as ei:
            sbx.apply_edit(ws, sbx.ScratchEdit(path=bad, op="write", data=b"x"))
        assert ei.value.code == "path_escape"


@pytest.mark.asyncio
async def test_materialize_is_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "sandbox_scratch_max_bytes", 8)
    data = b"y" * 64
    h = hashlib.sha256(data).digest()
    entries = [sbx.MaterializeEntry("big.bin", "file", h, len(data), False, None)]
    with pytest.raises(sbx.ScratchError) as ei:
        await sbx.materialize(entries, await _mem_reader({h: data}))
    assert ei.value.code == "scratch_too_large"


@pytest.mark.asyncio
async def test_delta_bounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "working_copy_max_changed_files", 1)
    ws = await sbx.materialize([], await _mem_reader({}))
    sbx.apply_edit(ws, sbx.ScratchEdit(path="a.txt", op="write", data=b"a"))
    sbx.apply_edit(ws, sbx.ScratchEdit(path="b.txt", op="write", data=b"b"))
    delta = sbx.compute_delta(ws, ws.files)
    assert delta.over_bounds is True


@pytest.mark.asyncio
async def test_bad_edit_op_is_named() -> None:
    ws = await sbx.materialize([], await _mem_reader({}))
    with pytest.raises(sbx.ScratchError) as ei:
        sbx.apply_edit(ws, sbx.ScratchEdit(path="a.txt", op="chmod"))
    assert ei.value.code == "bad_edit_op"


def test_sweep_orphan_containers_is_a_no_op_when_the_sandbox_is_disabled() -> None:
    """Under tar transport there is no host scratch tree left to sweep; what can leak is a
    container from a crashed worker, and only ours (label-filtered)."""
    assert settings.sandbox_kind != "docker"
    assert sbx.sweep_orphan_containers() == 0
    assert sbx_svc.sweep_orphan_scratch() == 0


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


async def _empty_ws():  # type: ignore[no-untyped-def]
    return await sbx.materialize([], await _mem_reader({}))


@pytest.mark.asyncio
async def test_disabled_sandbox_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "sandbox_kind", "disabled")
    out = await sbx.run_workspace(await _empty_ws(), "pytest -q")
    assert out.result.error == sbx.SANDBOX_DISABLED == "sandbox_disabled"


@pytest.mark.asyncio
async def test_daemon_unreachable_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import DockerException

    patch_docker(monkeypatch, from_env_error=DockerException(f"cannot connect {_LEAKY}"))
    out = await sbx.run_workspace(await _empty_ws(), "pytest -q")
    assert out.result.error == sbx.RUNTIME_DAEMON_UNREACHABLE == "runtime_daemon_unreachable"
    # The raw detail is kept separate from the named reason, for the operator log only.
    assert out.result.error_detail is not None and _LEAKY in out.result.error_detail


@pytest.mark.asyncio
async def test_image_missing_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import ImageNotFound

    patch_docker(monkeypatch, FakeSpec(create_error=ImageNotFound(f"no such image {_LEAKY}")))
    out = await sbx.run_workspace(await _empty_ws(), "pytest -q")
    assert out.result.error == sbx.RUNTIME_IMAGE_MISSING == "runtime_image_missing"


@pytest.mark.asyncio
async def test_container_start_failure_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    patch_docker(monkeypatch, FakeSpec(create_error=APIError(f"invalid resource {_LEAKY}")))
    out = await sbx.run_workspace(await _empty_ws(), "pytest -q")
    assert out.result.error == sbx.RUNTIME_START_FAILED == "runtime_start_failed"


@pytest.mark.asyncio
async def test_output_retrieval_failure_is_its_own_named_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    patch_docker(monkeypatch, FakeSpec(logs_error=APIError(f"stream broken {_LEAKY}")))
    out = await sbx.run_workspace(await _empty_ws(), "pytest -q")
    assert out.result.error == sbx.RUNTIME_TRANSPORT_FAILED == "runtime_transport_failed"


@pytest.mark.asyncio
async def test_unexpected_failure_carries_the_error_class(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    patch_docker(monkeypatch, FakeSpec(create_error=ValueError(f"boom {_LEAKY}")))
    out = await sbx.run_workspace(await _empty_ws(), "pytest -q")
    assert out.result.error == "error:ValueError"


@pytest.mark.asyncio
async def test_successful_run_reports_no_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    patch_docker(monkeypatch, FakeSpec(stdout=b"hello\n"))
    out = await sbx.run_workspace(await _empty_ws(), "echo hello")
    assert out.result.error is None
    assert out.result.exit_code == 0
    assert out.result.stdout == "hello\n"


def test_runtime_failure_reasons_are_distinct_and_in_the_contract() -> None:
    reasons = [
        sbx.SANDBOX_DISABLED,
        sbx.RUNTIME_DAEMON_UNREACHABLE,
        sbx.RUNTIME_IMAGE_MISSING,
        sbx.RUNTIME_START_FAILED,
        sbx.RUNTIME_TRANSPORT_FAILED,
        sbx.MEM_LIMIT,
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
        "mem_limit",
        "wall_timeout",
        "environment_missing_dependencies",
        "changeset_bounds",
        "path_escape",
        "fence_lost",
        "scratch_too_large",
        "credential_leak",
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
                    sbx.ScratchEdit(path="added.txt", op="write", data=b"new file\n"),
                    sbx.ScratchEdit(path="README.md", op="write", data=b"# changed\n"),
                    sbx.ScratchEdit(path="requirements.txt", op="delete"),
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
            # The overlay reflects the delta; the disposable copy is simply dropped.
            eff = await wc_svc.effective_tree(s, ctx, wc)
            assert eff["added.txt"].content_hash is not None
            assert "requirements.txt" not in eff
            assert out.runtime_session.container_ref is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sandbox_missing_dependency_still_persists_edits(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "sandbox_kind", "docker")

    async def _fake(ws: sbx.Workspace, command: str, **_kw: object) -> sbx.ExecOutcome:
        return sbx.ExecOutcome(
            sbx.RunResult("", "sh: ruff: not found", 127, False), files=dict(ws.files)
        )

    monkeypatch.setattr(sbx, "_execute_workspace", _fake)
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            req = sbx_svc.SandboxRequest(
                edits=[sbx.ScratchEdit(path="edited.txt", op="write", data=b"x\n")],
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
                    sbx.ScratchEdit(path="one.txt", op="write", data=b"1\n"),
                    sbx.ScratchEdit(path="two.txt", op="write", data=b"2\n"),
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
        (
            "create",
            ImageNotFound(f"no such image {_LEAKY}"),
            "runtime_image_missing",
            "image.txt",
        ),
        (
            "create",
            APIError(f"invalid resource limit {_LEAKY}"),
            "runtime_start_failed",
            "start.txt",
        ),
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
                    patch_docker(monkeypatch, from_env_error=exc)
                else:
                    patch_docker(monkeypatch, FakeSpec(create_error=exc))

                caplog.clear()
                with caplog.at_level(logging.WARNING, logger="app.services.project_sandbox"):
                    out = await sbx_svc.run_sandbox(
                        s,
                        ctx,
                        wc,
                        run_id=uuid.uuid4(),
                        request=sbx_svc.SandboxRequest(
                            edits=[sbx.ScratchEdit(path=path, op="write", data=b"x\n")],
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
    patch_docker(monkeypatch, FakeSpec(stdout=b"hello\n"))
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


# --- credential canary + hostile egress, end to end (config §1.7, ADR-047) ---


async def _artifact_bytes(s, ctx, wc):  # type: ignore[no-untyped-def]
    """Every artifact this working copy produced, as raw bytes."""
    from app.objectstore import build_object_store

    store = build_object_store()
    out: list[bytes] = []
    for art in await changes_svc.list_artifacts(
        s, ctx, project_id=wc.project_id, working_copy_id=wc.id
    ):
        blob = await s.get(StorageBlob, (ctx.tenant_id, wc.user_id, art.content_hash))
        if blob is not None:
            out.append(await store.get(blob.object_key))
    return out


@pytest.mark.asyncio
async def test_credential_canary_never_crosses_the_sandbox_boundary(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    """config §1.7's canary, end to end through the real orchestration boundary.

    A KEK-shaped secret sitting in the project tree must not appear in the tar, the change
    set, an artifact, the worker log or the model-facing observation — and holding it back
    must NOT be recorded as the sandbox deleting the user's file.
    """
    if not await ping_db():
        pytest.skip("database not reachable")
    canary = "sherpa-kek-canary-MDEyMzQ1Njc4OWFiY2RlZg=="
    client = patch_docker(monkeypatch, FakeSpec(stdout=b"ok\n"))
    assert client is not None

    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, wc = await _open_wc(s, ctx)
            # Put the secret into the PROJECT itself: write it, then Save so it becomes part
            # of the immutable base snapshot (head). That is the situation config §1.7
            # describes — a secret sitting in the project tree the sandbox is asked to work
            # on — and it is strictly stronger than leaving it as a pending overlay edit.
            await sbx_svc.run_sandbox(
                s,
                ctx,
                wc,
                run_id=uuid.uuid4(),
                request=sbx_svc.SandboxRequest(
                    edits=[
                        sbx.ScratchEdit(path=".env", op="write", data=f"KEK={canary}\n".encode()),
                        sbx.ScratchEdit(path="deploy/id_rsa", op="write", data=canary.encode()),
                    ]
                ),
            )
            await wc_svc.save(s, ctx, wc)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=wc.session_id)
            eff = await wc_svc.effective_tree(s, ctx, wc)
            assert ".env" in eff and "deploy/id_rsa" in eff
            assert wc.overlay_entry_count == 0  # the secret lives in the base, not the overlay

            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                out = await sbx_svc.run_sandbox(
                    s,
                    ctx,
                    wc,
                    run_id=uuid.uuid4(),
                    request=sbx_svc.SandboxRequest(command="python -c 'print(1)'"),
                )
            assert out.termination_reason == "done"

            # 1. never in the tar that reached the daemon
            container = client.containers.container
            assert container is not None
            assert canary.encode() not in container.ingested_tar
            assert ".env" not in container.ingested
            assert "deploy/id_rsa" not in container.ingested

            # 2. never copied into the overlay — held back is NOT deleted, and NOT re-added
            eff2 = await wc_svc.effective_tree(s, ctx, wc)
            assert eff2[".env"].content_hash == eff[".env"].content_hash
            assert eff2["deploy/id_rsa"].content_hash == eff["deploy/id_rsa"].content_hash
            assert wc.overlay_entry_count == 0

            # 3. never in the change set produced by this boundary
            if out.change_set_id is not None:
                cs = await changes_svc.get_change_set(
                    s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
                )
                entries, _ = await changes_svc.get_change_set_entries(s, ctx, cs)
                assert {e.path for e in entries} & {".env", "deploy/id_rsa"} == set()

            # 4. never in an artifact
            for blob in await _artifact_bytes(s, ctx, wc):
                assert canary.encode() not in blob

            # 5. never in the worker log, and never in the model-facing observation
            assert all(canary not in r.getMessage() for r in caplog.records)
            assert out.failure_note is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_hostile_egress_persists_nothing_from_the_container(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    """An egress tar that tries to escape ends the boundary with ``path_escape`` and no
    container-produced file reaches the overlay. The user's own explicit edit still
    persists: error-is-observation, never a crash and never silent data loss."""
    if not await ping_db():
        pytest.skip("database not reachable")
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in ("work/../../evil.txt", "work/planted.txt"):
            info = tarfile.TarInfo(name)
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))
    patch_docker(monkeypatch, FakeSpec(egress_tar=buf.getvalue()))

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
                    request=sbx_svc.SandboxRequest(
                        edits=[sbx.ScratchEdit(path="mine.txt", op="write", data=b"mine\n")],
                        command="python evil.py",
                    ),
                )
            assert out.termination_reason == "path_escape"
            assert out.failure_note is not None and "path_escape" in out.failure_note
            records = [r for r in caplog.records if r.name == "app.services.project_sandbox"]
            assert len(records) == 1

            eff = await wc_svc.effective_tree(s, ctx, wc)
            # Nothing the container produced landed; the explicit host-side edit did.
            assert "planted.txt" not in eff
            assert "evil.txt" not in eff
            assert eff["mine.txt"].content_hash is not None
        finally:
            await s.rollback()
