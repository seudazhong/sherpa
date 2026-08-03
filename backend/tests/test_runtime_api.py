"""Worker-owned RuntimeSession REST flow (Phase TR P4.4)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport

from app import queue
from app.auth import owner_ids
from app.config import settings
from app.db import ping_db
from app.main import app
from app.redis_client import ping_redis
from app.sandbox import runtime as sbx
from app.sandbox.transport import WorkspaceFile
from app.services import project_runtime as runtime_svc
from app.worker import (
    project_runtime_close_job,
    project_runtime_exec_job,
    project_runtime_open_job,
)
from tests.db_guard import drop_owner_tenant


class _RuntimeFake:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, WorkspaceFile]] = {}
        self.counter = 0

    async def open(self, ws: sbx.Workspace, *, session_label: str) -> sbx.RuntimeOpenOutcome:
        self.counter += 1
        ref = f"api-runtime-{self.counter}"
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
        files = dict(self.files[container_ref])
        if await cancel_requested():
            return sbx.RuntimeExecOutcome(
                sbx.RunResult("", "", -1, False),
                files=files,
                container_alive=False,
                cancelled=True,
            )
        files["api.txt"] = WorkspaceFile(command.encode("utf-8"))
        self.files[container_ref] = files
        await on_output("stdout", "done\n")
        return sbx.RuntimeExecOutcome(
            sbx.RunResult("done\n", "", 0, False),
            files=files,
            container_alive=True,
        )

    async def snapshot(self, container_ref: str) -> dict[str, WorkspaceFile]:
        return dict(self.files[container_ref])

    async def remove(self, container_ref: str | None) -> None:
        if container_ref is not None:
            self.files.pop(container_ref, None)


@pytest.mark.asyncio
async def test_runtime_rest_is_202_and_worker_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await drop_owner_tenant()
    fake = _RuntimeFake()
    enqueued: dict[str, list[uuid.UUID]] = {"open": [], "exec": [], "close": []}

    async def no_event(**kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    async def enqueue_open(tenant_id: uuid.UUID, runtime_id: uuid.UUID) -> None:
        enqueued["open"].append(runtime_id)

    async def enqueue_exec(tenant_id: uuid.UUID, exec_id: uuid.UUID) -> None:
        enqueued["exec"].append(exec_id)

    async def enqueue_close(tenant_id: uuid.UUID, runtime_id: uuid.UUID) -> None:
        enqueued["close"].append(runtime_id)

    monkeypatch.setattr(sbx, "open_runtime_workspace", fake.open)
    monkeypatch.setattr(sbx, "exec_runtime_command", fake.execute)
    monkeypatch.setattr(sbx, "snapshot_runtime_workspace", fake.snapshot)
    monkeypatch.setattr(sbx, "remove_runtime_container", fake.remove)
    monkeypatch.setattr(runtime_svc, "publish_transient_session_event", no_event)
    monkeypatch.setattr(queue, "enqueue_project_runtime_open", enqueue_open)
    monkeypatch.setattr(queue, "enqueue_project_runtime_exec", enqueue_exec)
    monkeypatch.setattr(queue, "enqueue_project_runtime_close", enqueue_close)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        login = await client.post(
            "/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password},
        )
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        project = await client.post(
            "/projects",
            json={"name": "Runtime API", "template_id": "python-basic"},
            headers=headers,
        )
        project_id = project.json()["id"]
        chat = await client.post(
            f"/projects/{project_id}/chats", json={"title": "runtime"}, headers=headers
        )
        session_id = chat.json()["id"]

        opened = await client.post(
            f"/projects/{project_id}/runtime",
            json={"session_id": session_id, "scope": "project"},
            headers=headers,
        )
        assert opened.status_code == 202, opened.text
        runtime_id = uuid.UUID(opened.json()["id"])
        assert opened.json()["state"] == "opening"
        assert enqueued["open"] == [runtime_id]

        tenant_id, _user_id = owner_ids()
        assert await project_runtime_open_job({}, str(tenant_id), str(runtime_id)) == "ready"
        ready = await client.get(f"/runtime/{runtime_id}")
        assert ready.json()["state"] == "ready"
        assert ready.json()["capabilities"]["tools"]["pytest"] == "8.3.3"

        queued = await client.post(
            f"/runtime/{runtime_id}/exec",
            json={"command": "write from api", "timeout_seconds": 30},
            headers=headers,
        )
        assert queued.status_code == 202, queued.text
        exec_id = uuid.UUID(queued.json()["id"])
        assert queued.json()["state"] == "queued"
        assert enqueued["exec"] == [exec_id]
        assert await project_runtime_exec_job({}, str(tenant_id), str(exec_id)) == "persisted"
        finished = await client.get(f"/runtime/{runtime_id}/exec/{exec_id}")
        assert finished.json()["state"] == "persisted"
        assert finished.json()["stdout_head"] == "done\n"
        assert finished.json()["change_set_id"] is not None

        queued_cancel = await client.post(
            f"/runtime/{runtime_id}/exec",
            json={"command": "cancel me"},
            headers=headers,
        )
        cancel_exec_id = uuid.UUID(queued_cancel.json()["id"])
        cancelled = await client.post(f"/runtime/{runtime_id}/cancel", json={}, headers=headers)
        assert cancelled.status_code == 202
        assert (
            await project_runtime_exec_job({}, str(tenant_id), str(cancel_exec_id)) == "cancelled"
        )

        closing = await client.delete(f"/runtime/{runtime_id}", headers=headers)
        assert closing.status_code == 200
        assert closing.json()["state"] == "closing"
        assert enqueued["close"] == [runtime_id]
        assert await project_runtime_close_job({}, str(tenant_id), str(runtime_id)) == "closed"
        closed = await client.get(f"/runtime/{runtime_id}")
        assert closed.json()["state"] == "closed"

    await drop_owner_tenant()
