"""Workspace Projects W3 sandbox orchestration (ADR-040 + ADR-039).

Two layers:
* **mechanics** (no DB) — materialize an effective tree into a fresh disposable scratch,
  host-side edits, path-escape rejection, scratch-vs-base delta, change-set bounds, and the
  orphan sweep. The docker container path is gated by ``SANDBOX_KIND`` (browser-exercised).
* **orchestration** (DB, in-memory object store) — one ``project_run`` boundary persists the
  scratch delta into the durable overlay fence-guarded; a missing dependency is an explicit
  named outcome; an over-bound delta persists nothing.
"""

from __future__ import annotations

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
            assert out.sandbox_run.state == "persisted"
            assert out.sandbox_run.termination_reason == "done"
            assert out.sandbox_run.persisted_boundary_at is not None
            # The overlay reflects the scratch delta; scratch is torn down.
            eff = await wc_svc.effective_tree(s, ctx, wc)
            assert eff["added.txt"].content_hash is not None
            assert "requirements.txt" not in eff
            assert out.sandbox_run.scratch_ref is None
            assert not psbx.scratch_dir_for(str(out.sandbox_run.id)).exists()
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_run_sandbox_missing_dependency_still_persists_edits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
            out = await sbx_svc.run_sandbox(s, ctx, wc, run_id=uuid.uuid4(), request=req)
            # Missing dependency is an EXPLICIT named outcome; the edit is still persisted.
            assert out.sandbox_run.termination_reason == "environment_missing_dependencies"
            assert out.sandbox_run.exit_code == 127
            assert out.sandbox_run.state == "persisted"
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
            assert out.sandbox_run.state == "failed"
            assert out.sandbox_run.termination_reason == "changeset_bounds"
            # No overlay persisted (never a silent partial).
            assert wc.overlay_entry_count == 0
        finally:
            await s.rollback()
