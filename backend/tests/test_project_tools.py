"""Workspace Project metadata/review tools after the P4 clean-break cutover."""

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

            created = await reg.get("project_create").execute(
                tctx, {"name": "Agent proj", "template_id": "python-basic"}
            )
            assert "Agent proj" in created.llm_content
            listing = await reg.get("project_list").execute(tctx, {})
            assert "Agent proj" in listing.llm_content
        finally:
            await s.rollback()


def test_project_tools_are_allow_policy() -> None:
    reg = build_default_registry()
    for name in ("project_list", "project_create", "project_review_changes"):
        assert evaluate(reg.get(name)) == "allow", name
    for absent in (
        "project_tree",
        "project_read",
        "project_run",
        "project_save",
        "project_checkpoint",
        "project_push",
        "project_delete",
    ):
        with pytest.raises(ToolError):
            reg.get(absent)


@pytest.mark.asyncio
async def test_fs_write_and_project_review_tools() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.models import Session as SessionModel
    from app.services import projects as svc

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
            write = await reg.get("fs_write").execute(
                tctx, {"path": "notes.txt", "content": "hello from the agent\n"}
            )
            assert "added notes.txt" in write.llm_content
            review = await reg.get("project_review_changes").execute(tctx, {})
            assert "notes.txt" in review.llm_content
            assert "user" in review.llm_content.lower()
        finally:
            await s.rollback()
