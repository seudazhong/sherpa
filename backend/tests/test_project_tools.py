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


def test_project_tools_are_allow_policy() -> None:
    reg = build_default_registry()
    for name in ("list_projects", "create_project", "project_tree", "project_read"):
        assert evaluate(reg.get(name)) == "allow", name
    # Destructive project execution/push are not exposed as tools in W2a.
    for absent in ("project_run", "project_push", "project_delete"):
        with pytest.raises(ToolError):
            reg.get(absent)
