"""Knowledge ↔ Drive lifecycle hooks + GC (ADR-036, KB2c).

Integration test — skips without a database (needs migration 0027). Covers the
overwrite→stale→auto-reindex→activate path (old version stays active until the new
one activates), the delete→tombstone hook, and the maintenance GC (tombstoned-source
purge + orphan snapshot sweep). Mock embeddings + in-memory object store; no network.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, ping_db
from app.models import KnowledgeSourceVersion, Tenant, User
from app.objectstore import build_object_store
from app.services import CallerContext, NotFound
from app.services import drive as drive_svc
from app.services import knowledge as ksvc
from app.services import knowledge_ingest as ki

_MD = b"# Doc\n\nOriginal budget approval content for the quarter."


async def _seed(s: AsyncSession) -> CallerContext:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="d@e.co", display_name="D", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="agent")


@pytest.mark.asyncio
async def test_overwrite_stales_then_reindex_activates_new_generation() -> None:
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
                    s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w"
                )
                == "done"
            )
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            v1 = src.active_version_id
            assert v1 is not None

            # Overwrite the backing file → hook marks stale, bumps generation, auto-enqueues.
            await drive_svc.upload(
                s,
                ctx,
                parent_id=None,
                name="doc.md",
                data=_MD + b"\n\n## New\n\nUpdated threshold to 30w.",
                content_type="text/markdown",
            )
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.status == "stale"
            assert src.desired_generation == 2
            assert src.active_version_id == v1  # old version still active/searchable

            # Process the auto-enqueued generation 2 → new version activates.
            assert (
                await ki.process_ingestion(
                    s, tenant_id=ctx.tenant_id, source_id=src.id, generation=2, lease_owner="w"
                )
                == "done"
            )
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            assert src.status == "ready"
            assert src.active_version_id is not None and src.active_version_id != v1
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_trash_tombstones_source() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="doc.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)
            await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w"
            )
            await drive_svc.trash(s, ctx, node.id)
            with pytest.raises(NotFound):
                await ksvc.get_source(s, ctx, source_id=src.id)
            assert await ksvc.list_sources(s, ctx) == []
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_gc_purges_tombstoned_and_sweeps_snapshot() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="doc.md", data=_MD, content_type="text/markdown"
            )
            src = await ksvc.create_source(s, ctx, file_id=node.id)
            await ki.process_ingestion(
                s, tenant_id=ctx.tenant_id, source_id=src.id, generation=1, lease_owner="w"
            )
            src = await ksvc.get_source(s, ctx, source_id=src.id)
            ver = await s.get(KnowledgeSourceVersion, (ctx.tenant_id, src.active_version_id))
            assert ver is not None
            snap_key = ver.snapshot_object_key
            assert snap_key in await build_object_store().list_keys("knowledge/")

            await drive_svc.trash(s, ctx, node.id)
            assert await ksvc.gc_tombstoned_sources(s) >= 1
            await ksvc.sweep_orphan_snapshots(s)
            assert snap_key not in await build_object_store().list_keys("knowledge/")
        finally:
            await s.rollback()
