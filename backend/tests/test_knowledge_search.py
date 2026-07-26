"""Hybrid knowledge retrieval (ADR-036, KB3b): lexical hit + citations, no-answer,
tenant isolation.

Deterministic integration test — skips without a database (needs migration 0027).
Uses mock embeddings (non-semantic, so the vector branch is plumbing only) + the real
zhparser `sherpa_text` FTS; the lexical-hit assertions skip where zhparser is
unavailable (e.g. CI on a vanilla image), where the lexical branch is dormant. Semantic
quality (real bge-m3) is verified in a live smoke, not here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import KnowledgeRetrievalEvidence, Tenant, User
from app.services import CallerContext
from app.services import drive as drive_svc
from app.services import knowledge as ksvc
from app.services import knowledge_ingest as ki
from app.services.knowledge_search import search_knowledge

_DOC = (
    "# 财务制度\n\n"
    "预算审批流程：单笔不超过5万由部门负责人审批，5到20万需财务总监会签，超过20万上报CFO。"
    "年度预算调整走季度复核。"
).encode()


async def _has_sherpa_text(s: AsyncSession) -> bool:
    return bool(
        await s.scalar(sql_text("SELECT 1 FROM pg_ts_config WHERE cfgname = 'sherpa_text'"))
    )


async def _seed_ingested(s: AsyncSession) -> CallerContext:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")
    node = await drive_svc.upload(
        s, ctx, parent_id=None, name="fin.md", data=_DOC, content_type="text/markdown"
    )
    src = await ksvc.create_source(s, ctx, file_id=node.id)
    assert (
        await ki.process_ingestion(
            s, tenant_id=tid, source_id=src.id, generation=1, lease_owner="w"
        )
        == "done"
    )
    return ctx


@pytest.mark.asyncio
async def test_search_lexical_hit_and_citations() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed_ingested(s)
            if not await _has_sherpa_text(s):
                pytest.skip("zhparser/sherpa_text unavailable — lexical branch dormant")

            res = await search_knowledge(s, ctx, query="预算 审批阈值", tool_call_id="call_ab12")
            assert res.sufficient is True
            assert res.hits
            top = res.hits[0]
            assert "lexical" in top.matched_by  # zhparser caught 预算/审批
            assert top.citation_ref == "K:call_ab12:1"
            assert top.heading == "财务制度"
            assert top.excerpt and "审批" in top.excerpt

            # Provider-visible excerpts persisted for replay (not the journal).
            n = await s.scalar(
                select(func.count())
                .select_from(KnowledgeRetrievalEvidence)
                .where(
                    KnowledgeRetrievalEvidence.retrieval_invocation_id
                    == res.retrieval_invocation_id
                )
            )
            assert n == len(res.hits)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_search_no_answer_is_insufficient() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed_ingested(s)
            # Unrelated query: no lexical match + mock vector similarity below the floor.
            res = await search_knowledge(s, ctx, query="量子力学 天体物理 星系红移")
            assert res.sufficient is False
            assert res.hits == []
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_search_tenant_isolation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            await _seed_ingested(s)
            other = CallerContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), actor="agent")
            res = await search_knowledge(s, other, query="预算 审批")
            assert res.hits == []
            assert res.sufficient is False
        finally:
            await s.rollback()
