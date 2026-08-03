"""Explicit RuntimeSession service over the durable Project working copy (ADR-048)."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import ProjectExecRun, Tenant, User
from app.models import Session as SessionModel
from app.permissions.policy import evaluate
from app.sandbox import runtime as sbx
from app.sandbox.transport import WorkspaceFile
from app.services import project_fs as fs_svc
from app.services import project_runtime as runtime_svc
from app.services import projects as projects_svc
from app.services.context import CallerContext
from app.tools import build_default_registry
from tests.db_guard import drop_tenant


async def _seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, CallerContext]:
    async with SessionLocal() as session:
        tid, uid = uuid.uuid4(), uuid.uuid4()
        session.add(
            Tenant(
                tenant_id=tid,
                slug=f"t-{tid.hex[:8]}",
                display_name="T",
                kind="personal",
            )
        )
        await session.flush()
        session.add(
            User(
                tenant_id=tid,
                id=uid,
                email="runtime@example.com",
                display_name="R",
                status="active",
            )
        )
        await session.flush()
        ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")
        project = await projects_svc.create_project(
            session, ctx, name="Runtime", template_id="python-basic"
        )
        sid = uuid.uuid4()
        session.add(
            SessionModel(
                tenant_id=tid,
                id=sid,
                user_id=uid,
                umo_key=f"web:chat:{sid}",
                channel="web",
                channel_installation_id="local",
                scope_type="chat",
                external_scope_id=str(sid),
                status="open",
                project_id=project.id,
            )
        )
        await session.commit()
    return tid, uid, sid, ctx


class _FakeRuntime:
    def __init__(self) -> None:
        self.opened = 0
        self.removed: list[str] = []
        self.files: dict[str, dict[str, WorkspaceFile]] = {}

    async def open(self, ws: sbx.Workspace, *, session_label: str) -> sbx.RuntimeOpenOutcome:
        self.opened += 1
        ref = f"fake-runtime-{self.opened}"
        self.files[ref] = dict(ws.sendable)
        return sbx.RuntimeOpenOutcome(
            sbx.RunResult("", "", 0, False, ingress_bytes=ws.total_bytes),
            container_ref=ref,
            image_digest="sha256:" + "ab" * 32,
            capabilities={"tools": [{"name": "pytest", "version": "8.3.3"}]},
        )

    async def execute(
        self,
        container_ref: str,
        command: str,
        *,
        timeout_seconds: int,
        on_output,
        cancel_requested,
    ) -> sbx.RuntimeExecOutcome:
        del timeout_seconds
        assert not await cancel_requested()
        files = dict(self.files[container_ref])
        if command == "write-generated":
            files["generated.txt"] = WorkspaceFile(b"generated\n")
            stdout, exit_code = "generated\n", 0
        elif command == "fail":
            stdout, exit_code = "1 failed\n", 1
        else:
            stdout, exit_code = "1 passed\n", 0
        await on_output("stdout", stdout)
        self.files[container_ref] = files
        return sbx.RuntimeExecOutcome(
            sbx.RunResult(stdout, "", exit_code, False),
            files=dict(files),
            container_alive=True,
        )

    async def snapshot(self, container_ref: str) -> dict[str, WorkspaceFile]:
        return dict(self.files[container_ref])

    async def remove(self, container_ref: str | None) -> None:
        if container_ref is not None:
            self.removed.append(container_ref)
            self.files.pop(container_ref, None)


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, fake: _FakeRuntime) -> None:
    async def no_event(**kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(sbx, "open_runtime_workspace", fake.open)
    monkeypatch.setattr(sbx, "exec_runtime_command", fake.execute)
    monkeypatch.setattr(sbx, "snapshot_runtime_workspace", fake.snapshot)
    monkeypatch.setattr(sbx, "remove_runtime_container", fake.remove)
    monkeypatch.setattr(runtime_svc, "publish_transient_session_event", no_event)


@pytest.mark.asyncio
async def test_runtime_reuses_container_then_rematerializes_after_host_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    tid, _uid, sid, ctx = await _seed()
    fake = _FakeRuntime()
    _patch_runtime(monkeypatch, fake)
    try:
        async with SessionLocal() as session:
            opened = await runtime_svc.open_runtime(session, ctx, session_id=sid, scope="project")
            runtime = opened.runtime_session
            assert runtime.state == "ready"
            assert runtime.container_ref == "fake-runtime-1"
            assert fake.opened == 1

            first = await runtime_svc.exec_runtime(
                session,
                ctx,
                session_id=sid,
                runtime_session_id=runtime.id,
                command="write-generated",
            )
            assert first.exec_run.state == "persisted"
            assert first.exec_run.persisted_boundary_at is not None
            assert first.exec_run.change_set_id is not None
            generated = await fs_svc.read_file(session, ctx, session_id=sid, path="generated.txt")
            assert generated.lines == ["generated"]

            failed_test = await runtime_svc.exec_runtime(
                session,
                ctx,
                session_id=sid,
                runtime_session_id=runtime.id,
                command="fail",
            )
            assert failed_test.exec_run.exit_code == 1
            assert failed_test.exec_run.termination_reason == "done"
            assert fake.opened == 1

            await fs_svc.write_file(
                session,
                ctx,
                session_id=sid,
                path="main.py",
                content="print('host edit')\n",
            )
            await session.refresh(runtime)
            assert runtime.state == "ready"
            assert runtime.container_ref is None
            assert "fake-runtime-1" in fake.removed

            passed = await runtime_svc.exec_runtime(
                session,
                ctx,
                session_id=sid,
                runtime_session_id=runtime.id,
                command="pass",
            )
            assert passed.exec_run.exit_code == 0
            assert fake.opened == 2
    finally:
        await drop_tenant(tid)


@pytest.mark.asyncio
async def test_cancel_signal_and_expired_runtime_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    tid, _uid, sid, ctx = await _seed()
    fake = _FakeRuntime()
    _patch_runtime(monkeypatch, fake)
    try:
        async with SessionLocal() as session:
            action = await runtime_svc.open_runtime(session, ctx, session_id=sid, scope="project")
            runtime = action.runtime_session
            runtime.state = "executing"
            exec_run = ProjectExecRun(
                tenant_id=tid,
                id=uuid.uuid4(),
                runtime_session_id=runtime.id,
                seq=1,
                command_text="sleep 999",
                command_preview="sleep 999",
                timeout_seconds=120,
                state="running",
            )
            session.add(exec_run)
            await session.commit()

            await runtime_svc.request_cancel(
                session,
                ctx,
                session_id=sid,
                runtime_session_id=runtime.id,
            )
            await session.refresh(exec_run)
            assert exec_run.cancel_requested_at is not None

            runtime.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
            await session.commit()
            recovered, refs = await runtime_svc.recover_expired(session)
            assert recovered == 1
            assert refs == ["fake-runtime-1"]
            await session.refresh(runtime)
            await session.refresh(exec_run)
            assert runtime.state == "failed"
            assert runtime.termination_reason == "error:RuntimeExpired"
            assert exec_run.state == "failed"
    finally:
        await drop_tenant(tid)


def test_runtime_tools_are_flat_full_and_sh_exec_asks() -> None:
    registry = build_default_registry()
    for name in ("runtime_open", "runtime_close", "sh_exec"):
        assert registry.is_visible(name, "full")
        assert not registry.is_visible(name, "safe")
    assert evaluate(registry.get("runtime_open"), {}) == "allow"
    assert evaluate(registry.get("runtime_close"), {}) == "allow"
    assert evaluate(registry.get("sh_exec"), {"command": "rm -rf /work"}) == "ask"


@pytest.mark.asyncio
async def test_concurrent_runtime_open_serializes_on_the_working_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    tid, _uid, sid, ctx = await _seed()
    fake = _FakeRuntime()
    original_open = fake.open

    async def delayed_open(ws: sbx.Workspace, *, session_label: str):
        await asyncio.sleep(0.1)
        return await original_open(ws, session_label=session_label)

    fake.open = delayed_open  # type: ignore[method-assign]
    _patch_runtime(monkeypatch, fake)

    async def open_once() -> uuid.UUID:
        async with SessionLocal() as session:
            action = await runtime_svc.open_runtime(session, ctx, session_id=sid, scope="project")
            return action.runtime_session.id

    try:
        first, second = await asyncio.gather(open_once(), open_once())
        assert first == second
        assert fake.opened == 1
    finally:
        await drop_tenant(tid)
