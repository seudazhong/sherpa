"""Archival/RAG passage memory (milestone 1c): add/search/dedupe/delete + tools.

Uses mock embeddings (provider_kind=mock → deterministic pseudo-vectors); the
lexical FTS branch drives deterministic retrieval. Integration test — skips
without a database (needs the pgvector extension from migration 0016).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Tenant, User
from app.services import CallerContext, NotFound
from app.services import passages as psvc
from app.tools import ToolContext, build_default_registry


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


@pytest.mark.asyncio
async def test_passage_add_search_dedupe_delete() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            p1 = await psvc.add_passage(s, ctx, text="I love hiking in the mountains on weekends")
            await psvc.add_passage(s, ctx, text="My cat Whiskers is a grey tabby")

            # Dedupe: identical text returns the same row.
            again = await psvc.add_passage(
                s, ctx, text="I love hiking in the mountains on weekends"
            )
            assert again.id == p1.id
            assert len(await psvc.list_passages(s, ctx)) == 2

            # Hybrid retrieval: the FTS branch surfaces the hiking passage for "hiking".
            hits = await psvc.search_passages(s, ctx, query="hiking mountains", k=5)
            assert hits and hits[0].id == p1.id

            await psvc.delete_passage(s, ctx, passage_id=p1.id)
            assert len(await psvc.list_passages(s, ctx)) == 1
            with pytest.raises(NotFound):
                await psvc.delete_passage(s, ctx, passage_id=p1.id)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_passage_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            out = await reg.get("memory_note").execute(
                tctx, {"text": "The Q3 project deadline is Friday the 14th"}
            )
            assert "noted" in out.llm_content

            res = await reg.get("memory_search").execute(tctx, {"query": "deadline"})
            assert "Friday" in res.llm_content
        finally:
            await s.rollback()
