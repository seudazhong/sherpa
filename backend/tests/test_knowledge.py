"""Knowledge source lifecycle (ADR-036, KB1): create/list/get/reindex/remove/stale.

Integration test — skips without a database (needs migration 0027). Exercises the
source/version/job lifecycle only; no zhparser/retrieval (that is KB3). The mock
embedding profile is recorded but nothing is embedded here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import (
    DriveNode,
    KnowledgeIngestionJob,
    KnowledgeSource,
    KnowledgeSourceVersion,
    Tenant,
    User,
)
from app.services import CallerContext, NotFound
from app.services import knowledge as ksvc


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tid, uid, fid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    s.add(
        DriveNode(
            tenant_id=tid,
            id=fid,
            user_id=uid,
            node_type="file",
            name="季度预算说明.docx",
            content_hash=b"\x00" * 32,
            size_bytes=1024,
            version=1,
        )
    )
    await s.flush()
    return tid, uid, fid


async def _count(s: AsyncSession, model: type, tid: uuid.UUID) -> int:
    return (
        await s.scalar(select(func.count()).select_from(model).where(model.tenant_id == tid)) or 0
    )


@pytest.mark.asyncio
async def test_knowledge_source_lifecycle() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, fid = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")

            # Create: a queued source + a building version + a queued job.
            src = await ksvc.create_source(s, ctx, file_id=fid)
            assert src.status == "queued"
            assert src.display_name == "季度预算说明.docx"
            assert src.desired_generation == 1
            assert await _count(s, KnowledgeSourceVersion, tid) == 1
            assert await _count(s, KnowledgeIngestionJob, tid) == 1

            ver = (
                await s.execute(
                    select(KnowledgeSourceVersion).where(KnowledgeSourceVersion.tenant_id == tid)
                )
            ).scalar_one()
            assert ver.status == "building"
            assert ver.expected_file_version == 1
            assert ver.snapshot_object_key.endswith("/1.snapshot")

            # Idempotent: re-adding the same file returns the same source (no dup).
            again = await ksvc.create_source(s, ctx, file_id=fid)
            assert again.id == src.id
            assert await _count(s, KnowledgeSource, tid) == 1

            assert len(await ksvc.list_sources(s, ctx)) == 1
            assert (await ksvc.get_source(s, ctx, source_id=src.id)).id == src.id

            # Reindex: bump generation + enqueue a fresh version/job.
            await ksvc.reindex_source(s, ctx, source_id=src.id)
            assert (await ksvc.get_source(s, ctx, source_id=src.id)).desired_generation == 2
            assert await _count(s, KnowledgeSourceVersion, tid) == 2
            assert await _count(s, KnowledgeIngestionJob, tid) == 2

            # Stale on file change.
            n = await ksvc.mark_stale_for_file(s, ctx, file_id=fid)
            assert n == 1
            assert (await ksvc.get_source(s, ctx, source_id=src.id)).status == "stale"
            assert (await ksvc.get_source(s, ctx, source_id=src.id)).desired_generation == 3

            # Remove: tombstone + cascade versions/jobs; Drive file untouched.
            await ksvc.remove_source(s, ctx, source_id=src.id)
            with pytest.raises(NotFound):
                await ksvc.get_source(s, ctx, source_id=src.id)
            assert await _count(s, KnowledgeSource, tid) == 0
            assert await _count(s, KnowledgeSourceVersion, tid) == 0
            assert await _count(s, KnowledgeIngestionJob, tid) == 0
            assert await s.get(DriveNode, (tid, fid)) is not None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_knowledge_tenant_isolation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, fid = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="agent")
            src = await ksvc.create_source(s, ctx, file_id=fid)

            other = CallerContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), actor="agent")
            with pytest.raises(NotFound):
                await ksvc.get_source(s, other, source_id=src.id)
            assert await ksvc.list_sources(s, other) == []
        finally:
            await s.rollback()
