"""Workspace Project agent tools (ADR-037, W2a): create/list/tree/read parity + policy.

Proves the agent can create blank/template projects and read them via tools, and that
all W2a project tools classify as ``allow`` (no destructive purge/run/push exposed).
Integration test — skips without Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Tenant, User
from app.permissions.policy import evaluate
from app.tools import ToolContext, build_default_registry
from app.tools.base import ToolError


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_project_tools_roundtrip() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            created = await reg.get("create_project").execute(
                tctx, {"name": "Agent proj", "template_id": "python-basic"}
            )
            assert "Agent proj" in created.llm_content
            pid = created.llm_content.split("id ")[-1].rstrip(").")

            listing = await reg.get("list_projects").execute(tctx, {})
            assert "Agent proj" in listing.llm_content

            tree = await reg.get("project_tree").execute(tctx, {"project_id": pid})
            assert "main.py" in tree.llm_content

            read = await reg.get("project_read").execute(
                tctx, {"project_id": pid, "path": "main.py"}
            )
            assert "hello, sherpa" in read.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_project_tree_tool_marks_truncated_page(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.services import projects as svc

    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)
            pid, sid = uuid.uuid4(), uuid.uuid4()
            entry = svc.TreeEntry(path="backend", entry_kind="dir", size_bytes=0, executable=False)

            async def fake_partial(db, ctx, **kw):  # type: ignore[no-untyped-def]
                return svc.ProjectTree(
                    project_id=pid, snapshot_id=sid, entries=[entry], truncated=True
                )

            monkeypatch.setattr("app.tools.project_tools.svc.get_tree", fake_partial)
            out = await reg.get("project_tree").execute(tctx, {"project_id": str(pid)})
            # A truncated page must NOT read as a complete tree.
            assert "PARTIAL" in out.llm_content
            assert "not proof" in out.llm_content.lower()
            assert "path" in out.llm_content.lower()
            assert "complete listing" not in out.llm_content

            async def fake_full(db, ctx, **kw):  # type: ignore[no-untyped-def]
                return svc.ProjectTree(
                    project_id=pid, snapshot_id=sid, entries=[entry], truncated=False
                )

            monkeypatch.setattr("app.tools.project_tools.svc.get_tree", fake_full)
            out2 = await reg.get("project_tree").execute(tctx, {"project_id": str(pid)})
            assert "complete listing" in out2.llm_content
            assert "PARTIAL" not in out2.llm_content
        finally:
            await s.rollback()


def test_project_tools_are_allow_policy() -> None:
    reg = build_default_registry()
    # W2a own-data tools + the W3 sandbox/review tools all classify as allow.
    for name in (
        "list_projects",
        "create_project",
        "project_tree",
        "project_read",
        "project_run",
        "project_review_changes",
    ):
        assert evaluate(reg.get(name)) == "allow", name
    # Save-to-head, push (W4), and destructive delete are NOT agent tools.
    for absent in ("project_save", "project_checkpoint", "project_push", "project_delete"):
        with pytest.raises(ToolError):
            reg.get(absent)


@pytest.mark.asyncio
async def test_project_run_and_review_tools(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.config import settings
    from app.models import Session as SessionModel
    from app.services import projects as svc

    monkeypatch.setattr(settings, "sandbox_scratch_root", str(tmp_path / "scratch"))
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            from app.services.context import CallerContext

            cc = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            project = await svc.create_project(s, cc, name="Run proj", template_id="python-basic")
            sid = uuid.uuid4()
            s.add(
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
                    admitted_seq=1,
                )
            )
            await s.flush()

            reg = build_default_registry()
            tctx = ToolContext(
                tenant_id=tid, user_id=uid, session_id=sid, run_id=uuid.uuid4(), session=s
            )
            run = await reg.get("project_run").execute(
                tctx,
                {"writes": [{"path": "notes.txt", "content": "hello from the agent\n"}]},
            )
            assert "Pending changes" in run.llm_content
            assert "+1" in run.llm_content
            # Review lists the staged change; Save stays a human action.
            review = await reg.get("project_review_changes").execute(tctx, {})
            assert "notes.txt" in review.llm_content
            assert "user" in review.llm_content.lower()
        finally:
            await s.rollback()
