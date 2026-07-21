"""Personal files service + tools (milestone 2): put/read/list/delete + traversal.

Uses the in-memory object store (storage_kind=memory default). Integration test —
skips without a database (needs the files table from migration 0017).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Tenant, User
from app.services import CallerContext, Invalid, NotFound
from app.services import files as fsvc
from app.tools import ToolContext, build_default_registry


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_file_service_roundtrip_and_traversal() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            row = await fsvc.put_file(s, ctx, path="notes/todo.md", data=b"hello")
            assert row.size_bytes == 5 and row.version == 1

            _r, data = await fsvc.read_file(s, ctx, path="notes/todo.md")
            assert data == b"hello"

            # Overwrite same path bumps the version.
            row2 = await fsvc.put_file(s, ctx, path="notes/todo.md", data=b"hello world")
            assert row2.version == 2 and row2.size_bytes == 11
            assert len(await fsvc.list_files(s, ctx)) == 1

            # Path traversal is rejected.
            with pytest.raises(Invalid):
                await fsvc.put_file(s, ctx, path="../etc/passwd", data=b"x")

            await fsvc.delete_file(s, ctx, path="notes/todo.md")
            with pytest.raises(NotFound):
                await fsvc.read_file(s, ctx, path="notes/todo.md")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_file_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            w = await reg.get("file_write").execute(
                tctx, {"path": "a.txt", "content": "content here"}
            )
            assert "wrote" in w.llm_content

            r = await reg.get("file_read").execute(tctx, {"path": "a.txt"})
            assert "content here" in r.llm_content

            listed = await reg.get("file_list").execute(tctx, {})
            assert "a.txt" in listed.llm_content

            out = await reg.get("file_delete").execute(tctx, {"path": "a.txt"})
            assert "deleted" in out.llm_content
        finally:
            await s.rollback()
