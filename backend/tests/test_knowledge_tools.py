"""Knowledge agent tools + policy (ADR-036, KB4).

Integration test — skips without a database. Proves the 5 tools drive the same
capability layer, the ALLOWED policy classifies them correctly (search/list/add/
reindex → allow, remove → ask), and a real add → ingest → search yields cited hits
(lexical assertions skip where zhparser/sherpa_text is unavailable).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Tenant, User
from app.permissions.policy import evaluate
from app.services import CallerContext
from app.services import knowledge as ksvc
from app.services import knowledge_ingest as ki
from app.tools import ToolContext, build_default_registry


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


async def _has_sherpa_text(s: AsyncSession) -> bool:
    return bool(
        await s.scalar(sql_text("SELECT 1 FROM pg_ts_config WHERE cfgname = 'sherpa_text'"))
    )


_FIN_MD = "# 财务制度\n\n预算审批流程：单笔不超过5万由部门负责人审批，超过20万上报CFO。"


def test_knowledge_tool_policy() -> None:
    reg = build_default_registry()
    assert evaluate(reg.get("knowledge.search")) == "allow"
    assert evaluate(reg.get("knowledge.list_sources")) == "allow"
    assert evaluate(reg.get("knowledge.add_source")) == "allow"
    assert evaluate(reg.get("knowledge.reindex")) == "allow"
    assert evaluate(reg.get("knowledge.remove_source")) == "ask"  # destructive → approval


@pytest.mark.asyncio
async def test_knowledge_tools_add_ingest_search() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed(s)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)
            cc = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            await reg.get("drive.write").execute(tctx, {"path": "docs/fin.md", "content": _FIN_MD})
            added = await reg.get("knowledge.add_source").execute(tctx, {"path": "docs/fin.md"})
            assert "Knowledge" in added.llm_content

            srcs = await ksvc.list_sources(s, cc)
            assert len(srcs) == 1
            assert (
                await ki.process_ingestion(
                    s, tenant_id=tid, source_id=srcs[0].id, generation=1, lease_owner="w"
                )
                == "done"
            )

            listed = await reg.get("knowledge.list_sources").execute(tctx, {})
            assert "fin.md" in listed.llm_content

            res = await reg.get("knowledge.search").execute(tctx, {"query": "预算 审批阈值"})
            if await _has_sherpa_text(s):
                assert "K:" in res.llm_content  # citation reference present
                assert "审批" in res.llm_content
        finally:
            await s.rollback()
