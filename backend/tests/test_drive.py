"""Personal Drive service: folders, upload/dedupe/quota, versions, move, trash, GC.

Integration test — skips when no database is reachable; rolls back. Uses the
in-memory object store (settings.storage_kind defaults to "memory" in tests).
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import StorageBlob, Tenant, User
from app.services import drive as svc
from app.services.context import CallerContext
from app.services.errors import Conflict, Forbidden, InsufficientStorage, NotFound, VersionConflict


async def _seed_owner(s) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return tid, uid


def _ctx(tid: uuid.UUID, uid: uuid.UUID, actor: str = "user") -> CallerContext:
    return CallerContext(tenant_id=tid, user_id=uid, actor=actor)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_folders_upload_dedupe_and_quota() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = _ctx(tid, uid)

            folder = await svc.create_folder(s, ctx, parent_id=None, name="projects")
            assert folder.node_type == "folder"

            a = await svc.upload(s, ctx, parent_id=folder.id, name="a.txt", data=b"hello world")
            assert a.size_bytes == 11
            summary = await svc.storage_summary(s, ctx)
            assert summary.used_bytes == 11

            # Same bytes under a different name → dedupe: used_bytes unchanged.
            await svc.upload(s, ctx, parent_id=None, name="copy.txt", data=b"hello world")
            summary = await svc.storage_summary(s, ctx)
            assert summary.used_bytes == 11

            # Duplicate name in the same folder → conflict.
            with pytest.raises(Conflict):
                await svc.create_folder(s, ctx, parent_id=None, name="projects")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_quota_exceeded_returns_507() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = _ctx(tid, uid)
            acct = await svc._get_account(s, ctx, uid)
            acct.quota_bytes = 5
            await s.flush()
            with pytest.raises(InsufficientStorage):
                await svc.upload(s, ctx, parent_id=None, name="big.txt", data=b"too many bytes")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_overwrite_keeps_version_and_restore() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = _ctx(tid, uid)
            n = await svc.upload(s, ctx, parent_id=None, name="doc.md", data=b"v1 content")
            assert n.version == 1
            n = await svc.upload(s, ctx, parent_id=None, name="doc.md", data=b"v2 content here")
            assert n.version == 2

            versions = await svc.list_versions(s, ctx, n.id)
            assert [v.version for v in versions] == [1]

            _node, data = await svc.read_node(s, ctx, n.id)
            assert data == b"v2 content here"

            restored = await svc.restore_version(s, ctx, n.id, 1)
            assert restored.version == 3
            _node2, data2 = await svc.read_node(s, ctx, n.id)
            assert data2 == b"v1 content"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_move_rename_and_version_conflict() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = _ctx(tid, uid)
            folder = await svc.create_folder(s, ctx, parent_id=None, name="dst")
            f = await svc.upload(s, ctx, parent_id=None, name="x.txt", data=b"data")

            moved = await svc.move(
                s,
                ctx,
                f.id,
                if_version=f.version,
                parent_id=folder.id,
                new_parent_given=True,
                name="y.txt",
            )
            assert moved.parent_id == folder.id
            assert moved.name == "y.txt"

            with pytest.raises(VersionConflict):
                await svc.move(s, ctx, f.id, if_version=999, name="z.txt")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_trash_restore_and_purge_human_only() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = _ctx(tid, uid)
            agent = _ctx(tid, uid, actor="agent")

            folder = await svc.create_folder(s, ctx, parent_id=None, name="box")
            f = await svc.upload(s, ctx, parent_id=folder.id, name="in.txt", data=b"body")

            trashed = await svc.trash(s, ctx, folder.id)
            assert trashed.trashed_at is not None
            child = await svc.get_node(s, ctx, f.id)
            assert child.trashed_at is not None  # subtree trashed

            # Trash listing shows the top-most trashed node, not its children.
            trash_page = await svc.list_nodes(s, ctx, trashed=True)
            trash_ids = [n.id for n in trash_page.items]
            assert folder.id in trash_ids
            assert f.id not in trash_ids  # child hidden under its trashed parent

            summary = await svc.storage_summary(s, ctx)
            assert summary.trashed_bytes == 4  # bytes now only under trash

            restored = await svc.restore(s, ctx, folder.id)
            assert restored.trashed_at is None
            child = await svc.get_node(s, ctx, f.id)
            assert child.trashed_at is None

            # Agent cannot purge.
            with pytest.raises(Forbidden):
                await svc.purge(s, agent, folder.id)

            # Human purge reclaims bytes.
            await svc.trash(s, ctx, folder.id)
            await svc.purge(s, ctx, folder.id)
            with pytest.raises(NotFound):
                await svc.get_node(s, ctx, f.id)
            summary = await svc.storage_summary(s, ctx)
            assert summary.used_bytes == 0
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_path_write_read_and_gc() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            ctx = _ctx(tid, uid)

            node = await svc.write_path(s, ctx, path="notes/2026/todo.md", data=b"remember")
            assert node.name == "todo.md"
            path = await svc.node_path(s, ctx, node)
            assert path == "notes/2026/todo.md"

            resolved = await svc.resolve_file_by_path(s, ctx, "notes/2026/todo.md")
            assert resolved.id == node.id

            # Purge → blob becomes unreferenced; GC skips until retention passes.
            content_hash = node.content_hash
            await svc.purge(s, ctx, resolved.id)
            blob = await s.get(StorageBlob, (tid, uid, content_hash))
            assert blob is not None and blob.ref_count == 0

            # Force the blob past retention and GC it.
            blob.unreferenced_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
            await s.flush()
            removed = await svc.gc_unreferenced_blobs(s)
            assert removed >= 1
            assert await s.get(StorageBlob, (tid, uid, content_hash)) is None
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_cross_user_isolation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid = await _seed_owner(s)
            other = uuid.uuid4()
            s.add(
                User(tenant_id=tid, id=other, email="x@e.co", display_name="X", status="disabled")
            )
            await s.flush()
            ctx = _ctx(tid, uid)
            other_ctx = _ctx(tid, other)

            n = await svc.upload(s, ctx, parent_id=None, name="secret.txt", data=b"mine")
            with pytest.raises(NotFound):
                await svc.get_node(s, other_ctx, n.id)
            page = await svc.list_nodes(s, other_ctx)
            assert page.items == []
        finally:
            await s.rollback()
