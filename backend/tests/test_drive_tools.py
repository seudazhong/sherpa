"""Personal Drive agent tools (ADR-030, W1): agent parity over the same service.

Proves the agent can create folders, write/read/list/search/move/trash via tools,
and that permanent purge is intentionally NOT exposed as a tool. Integration test —
skips without Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Tenant, User
from app.tools import ToolContext, build_default_registry


async def _seed_base(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_drive_tools_roundtrip() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            wrote = await reg.get("drive_write").execute(
                tctx, {"path": "projects/notes.md", "content": "hello"}
            )
            assert "projects/notes.md" in wrote.llm_content

            read = await reg.get("drive_read").execute(tctx, {"path": "projects/notes.md"})
            assert read.llm_content == "hello"

            listing = await reg.get("drive_list").execute(tctx, {"path": "projects"})
            assert "notes.md" in listing.llm_content

            found = await reg.get("drive_search").execute(tctx, {"query": "notes"})
            assert "projects/notes.md" in found.llm_content

            await reg.get("drive_make_folder").execute(tctx, {"path": "archive/2026"})
            moved = await reg.get("drive_move").execute(
                tctx, {"path": "projects/notes.md", "to": "archive/2026/old.md"}
            )
            assert "archive/2026/old.md" in moved.llm_content

            trashed = await reg.get("drive_trash").execute(tctx, {"path": "archive/2026/old.md"})
            assert "trashed" in trashed.llm_content

            # Purge is human-only: no such agent tool is registered.
            assert not reg.is_visible("drive_purge", "full")
        finally:
            await s.rollback()
