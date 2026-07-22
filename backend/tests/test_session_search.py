"""Session search projection: reindex + fused English/CJK/trigram retrieval.

Integration test — skips when no database is reachable; rolls back.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import Message, Part, Run, SessionSearchEntry, Tenant, User
from app.models import Session as SessionModel
from app.search import reindex_session, search
from app.services.context import CallerContext


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tid, uid, sid, rid = (uuid.uuid4() for _ in range(4))
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
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
            title="Postgres migration plan",
        )
    )
    await s.flush()
    s.add(Run(tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1"))
    await s.flush()

    async def add_msg(seq: int, role: str, txt: str) -> None:
        mid = uuid.uuid4()
        s.add(
            Message(
                tenant_id=tid,
                id=mid,
                session_id=sid,
                run_id=rid,
                author_user_id=uid if role == "user" else None,
                seq=seq,
                role=role,
            )
        )
        await s.flush()
        s.add(
            Part(
                tenant_id=tid,
                id=uuid.uuid4(),
                message_id=mid,
                ordinal=0,
                kind="text",
                content_redacted={"text": txt},
            )
        )
        await s.flush()

    await add_msg(1, "user", "How should I migrate the Postgres database safely?")
    await add_msg(2, "assistant", "Use expand-migrate-contract with a backup first.")
    await add_msg(
        3, "user", "帮我总结一下这个数据库迁移计划"
    )  # Chinese: summarize the DB migration plan
    return tid, uid, sid


@pytest.mark.asyncio
async def test_search_english_and_deep_link_anchor() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed(s)
            await reindex_session(s, tid, sid)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")

            hits = await search(s, ctx, "migrate database")
            assert hits, "expected a match for an English query"
            assert hits[0].session_id == sid
            # A message match carries a typed message anchor for deep-linking.
            assert any(h.anchor_kind == "message" for h in hits) or hits[0].anchor_kind in (
                "message",
                "session",
            )
            assert hits[0].snippet
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_search_chinese_bigrams() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed(s)
            await reindex_session(s, tid, sid)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")

            hits = await search(s, ctx, "数据库迁移")  # DB migration
            assert hits, "expected a CJK bigram match"
            assert hits[0].session_id == sid
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_reindex_is_idempotent_and_scoped() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed(s)
            await reindex_session(s, tid, sid)
            first = (
                await s.execute(
                    select(SessionSearchEntry.id).where(
                        SessionSearchEntry.tenant_id == tid,
                        SessionSearchEntry.session_id == sid,
                    )
                )
            ).all()
            # Rebuild produces the same number of entries (deterministic).
            await reindex_session(s, tid, sid)
            second = (
                await s.execute(
                    select(SessionSearchEntry.id).where(
                        SessionSearchEntry.tenant_id == tid,
                        SessionSearchEntry.session_id == sid,
                    )
                )
            ).all()
            assert len(first) == len(second) and len(first) >= 4

            # A different user sees no results.
            other = CallerContext(tenant_id=tid, user_id=uuid.uuid4(), actor="user")
            assert await search(s, other, "migrate database") == []
        finally:
            await s.rollback()
