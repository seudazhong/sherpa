"""Candidate tools + service (m-tools T3).

Proves the shared capability layer works through all three paths: the service
directly, the agent tools via the registry, and end-to-end through the core loop
(agent emits accept_candidate → policy allows → todo created). Integration test —
skips without Postgres+Redis; flush + rollback (no commit needed for assertions).
"""

from __future__ import annotations

import datetime
import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import Candidate, Connector, ConnectorItem, Run, Tenant, Todo, User
from app.models import Session as SessionModel
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.services import CallerContext, NotFound, VersionConflict, candidates
from app.tools import ToolContext, build_default_registry

_JSON = (
    '{"candidates": [{"title": "Review Q3 budget", "description": "Send feedback",'
    ' "due_at": "2026-07-24T09:00:00Z", "priority": "high", "confidence": 0.9,'
    ' "rationale": "Manager asked", "source_excerpt": "review the Q3 budget"}]}'
)


async def _seed_base(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return tid, uid


async def _seed_candidate(s: AsyncSession, tid: uuid.UUID, uid: uuid.UUID) -> uuid.UUID:
    cid, iid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s.add(
        Connector(
            tenant_id=tid,
            id=cid,
            user_id=uid,
            kind="gmail",
            channel_installation_id=f"gmail:{cid}",
            external_account_id=f"o-{cid}@g.com",
            status="pending_oauth",
        )
    )
    await s.flush()
    item = ConnectorItem(
        tenant_id=tid,
        id=iid,
        connector_id=cid,
        provider_item_id=f"m-{uuid.uuid4().hex[:8]}",
        revision="1",
        received_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
        content_digest=hashlib.sha256(iid.bytes).digest(),
        content_json={"from": "boss@acme.com", "subject": "Q3", "snippet": "review"},
        is_latest=True,
    )
    s.add(item)
    await s.flush()
    s.add(Run(tenant_id=tid, id=rid, run_kind="candidate_extraction", prompt_version="x"))
    await s.flush()
    result = await candidates_extraction(s, item, rid)
    cand = (
        await s.execute(
            select(Candidate).where(Candidate.tenant_id == tid, Candidate.extraction_id == result)
        )
    ).scalar_one()
    return cand.id


async def candidates_extraction(s: AsyncSession, item: ConnectorItem, rid: uuid.UUID) -> uuid.UUID:
    from app.connectors.analysis import run_extraction

    result = await run_extraction(
        s,
        connector_item=item,
        run_id=rid,
        provider=MockProvider(script=[[TextDelta(_JSON), Finish("stop")]]),
        provider_name="mock",
        model="mock-v1",
    )
    return result.extraction_id


@pytest.mark.asyncio
async def test_candidate_service_accept_and_errors() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            cid = await _seed_candidate(s, tid, uid)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            page = await candidates.list_candidates(s, ctx)
            cand = next(c for c in page.items if c.id == cid)
            assert cand.status == "pending"

            with pytest.raises(NotFound):
                await candidates.accept_candidate(s, ctx, candidate_id=uuid.uuid4(), if_version=1)
            with pytest.raises(VersionConflict):
                await candidates.accept_candidate(s, ctx, candidate_id=cid, if_version=999)

            result = await candidates.accept_candidate(
                s, ctx, candidate_id=cid, if_version=cand.version
            )
            assert result.candidate.status == "accepted"
            assert result.todo.source_candidate_id == cid
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_candidate_tools_via_registry() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            cid = await _seed_candidate(s, tid, uid)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            listing = await reg.get("inbox_list_candidates").execute(tctx, {})
            assert str(cid) in listing.llm_content

            accepted = await reg.get("inbox_accept").execute(
                tctx, {"candidate_id": str(cid), "if_version": 1}
            )
            assert "accepted candidate" in accepted.llm_content
            cand = await s.get(Candidate, (tid, cid))
            assert cand is not None and cand.status == "accepted"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_accept_candidate_with_patch_edits_first() -> None:
    """`accept_candidate` absorbed `edit_candidate` (Phase TR P2.0, backlog B-10):
    passing any of title/description/due_at/priority edits before accepting."""
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            cid = await _seed_candidate(s, tid, uid)
            reg = build_default_registry()
            tctx = ToolContext(tenant_id=tid, user_id=uid, session=s)

            out = await reg.get("inbox_accept").execute(
                tctx,
                {
                    "candidate_id": str(cid),
                    "if_version": 1,
                    "title": "Renamed by the agent",
                    "priority": "high",
                },
            )
            assert "edited + accepted candidate" in out.llm_content
            cand = await s.get(Candidate, (tid, cid))
            assert cand is not None and cand.status == "edited"
            todo = (
                await s.execute(select(Todo).where(Todo.source_candidate_id == cid))
            ).scalar_one()
            assert todo.title == "Renamed by the agent" and todo.priority == "high"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_loop_agent_accepts_candidate() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_base(s)
            sid, rid = uuid.uuid4(), uuid.uuid4()
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
                )
            )
            await s.flush()
            run = Run(
                tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1"
            )
            s.add(run)
            await s.flush()
            cid = await _seed_candidate(s, tid, uid)

            provider = MockProvider(
                script=[
                    [
                        ToolCall(
                            id="c1",
                            name="inbox_accept",
                            args={"candidate_id": str(cid), "if_version": 1},
                        ),
                        Finish("tool_use"),
                    ],
                    [TextDelta("Accepted."), Finish("stop")],
                ]
            )
            reason = await execute_run(
                s, run=run, provider=provider, registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"

            cand = await s.get(Candidate, (tid, cid))
            assert cand is not None and cand.status == "accepted"  # policy allowed the write
            todo = (
                await s.execute(
                    select(Todo).where(Todo.tenant_id == tid, Todo.source_candidate_id == cid)
                )
            ).scalar_one()
            assert todo.title == "Review Q3 budget"
        finally:
            await s.rollback()
