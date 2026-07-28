"""Durable knowledge ingestion (ADR-036, KB2b): snapshot → parse → chunk → embed →
fenced activate, plus the named failure / file-changed / superseded exits.

Integration test — skips without a database (needs migration 0027). Uses the mock
embedding profile (deterministic 1024-d) and the in-memory object store; no network.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import (
    KnowledgeChunk,
    KnowledgeSourceVersion,
    Tenant,
    User,
)
from app.services import CallerContext
from app.services import drive as drive_svc
from app.services import knowledge as ksvc
from app.services import knowledge_ingest as ki

_MD = (
    b"# Budget\n\n## 3.2 Approval\n\nSingle spend under 5w by the dept lead; over 20w to CFO.\n\n"
    b"# Notes\n\nQuarterly review applies."
)


async def _seed(s: AsyncSession) -> CallerContext:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="agent")


@pytest.mark.asyncio
async def test_ingest_happy_path_activates() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="budget.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)

            reason = await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w1"
            )
            assert reason == "done"

            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.status == "ready"
            assert src.active_version_id is not None

            ver = await s.get(KnowledgeSourceVersion, (ctx.tenant_id, src.active_version_id))
            assert ver is not None and ver.status == "ready"
            assert ver.chunk_count > 0 and ver.language == "en"

            n = await s.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.version_id == ver.id)
            )
            assert n == ver.chunk_count
            row = await s.scalar(
                select(KnowledgeChunk).where(KnowledgeChunk.version_id == ver.id).limit(1)
            )
            assert row is not None and len(list(row.embedding)) == 1024
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_ingest_unsupported_file_fails_named() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s,
                ctx,
                parent_id=None,
                name="x.bin",
                data=b"\x00\x01\x02",
                content_type="application/octet-stream",
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)
            reason = await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w1"
            )
            assert reason == "unsupported_type"
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.status == "failed"
            assert src.active_version_id is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_ingest_file_changed_terminates() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="doc.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)
            # Defensive branch: the file changed out-of-band (bypassing the overwrite
            # hook) so the version's expected hash no longer matches the readable file,
            # while its generation is still current. Snapshot must refuse to index it.
            ver = await s.scalar(
                select(KnowledgeSourceVersion).where(
                    KnowledgeSourceVersion.tenant_id == ctx.tenant_id,
                    KnowledgeSourceVersion.source_id == src.id,
                    KnowledgeSourceVersion.generation == 1,
                )
            )
            assert ver is not None
            ver.expected_file_hash = b"\x22" * 32
            await s.flush()
            reason = await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w1"
            )
            assert reason == "file_changed"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_ingest_superseded_generation_no_activate() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="doc.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)
            # A newer generation was requested (e.g. reindex) before this job ran.
            src.desired_generation = 2
            await s.flush()
            reason = await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w1"
            )
            assert reason == "superseded"
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.active_version_id is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_ingest_embedding_failure_is_a_named_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead or too-slow embedding backend must terminate the job with a named
    reason — not bubble an exception into arq and retry into the same timeout."""
    if not await ping_db():
        pytest.skip("database not reachable")

    async def boom(texts: list[str], *, progress: object = None) -> list[list[float]]:
        raise RuntimeError("embedding backend unreachable")

    monkeypatch.setattr(ki, "embed_texts", boom)
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="doc.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)

            reason = await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w1"
            )

            assert reason == "embedding_failed"
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.status == "failed"
            assert src.active_version_id is None
            ver = await s.scalar(
                select(KnowledgeSourceVersion).where(
                    KnowledgeSourceVersion.source_id == src.id,
                    KnowledgeSourceVersion.generation == 1,
                )
            )
            assert ver is not None
            assert ver.status == "failed" and ver.failure_code == "embedding_failed"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_failed_rebuild_keeps_the_previous_version_searchable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-safe: a broken re-index must not take the live index down with it."""
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="doc.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)
            assert (
                await ki.process_ingestion(
                    s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w1"
                )
                == "done"
            )
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            active_v1 = src.active_version_id
            assert active_v1 is not None

            await ksvc.reindex_source(s, ctx, source_id=src.id)
            await s.flush()

            async def boom(texts: list[str], *, progress: object = None) -> list[list[float]]:
                raise RuntimeError("embedding backend unreachable")

            monkeypatch.setattr(ki, "embed_texts", boom)
            reason = await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=2, lease_owner="w1"
            )

            assert reason == "embedding_failed"
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.active_version_id == active_v1  # v1 still serves searches
        finally:
            await s.rollback()
