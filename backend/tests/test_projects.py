"""Workspace Projects service (ADR-037, W2a): blank/template create, snapshots,
shared-blob quota, Project-bound Chat binding + immutability, tree/read.

Integration test — skips without a database (needs migration 0028). Uses the in-memory
object store; rolls back.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import Tenant, User
from app.services import projects as svc
from app.services.archive import ArchiveEntry
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


@pytest.mark.asyncio
async def test_blank_project_has_empty_snapshot() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await svc.create_project(s, ctx, name="Blank one")
            assert project.current_snapshot_id is not None
            assert project.used_bytes == 0
            tree = await svc.get_tree(s, ctx, project_id=project.id)
            assert tree.entries == []
            item = await svc.get_list_item(s, ctx, project_id=project.id)
            assert item.import_status == "ready"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_template_project_copies_entries_and_charges_quota() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await svc.create_project(s, ctx, name="Py", template_id="python-basic")
            tree = await svc.get_tree(s, ctx, project_id=project.id)
            paths = {e.path for e in tree.entries}
            assert "main.py" in paths
            assert "README.md" in paths
            # A non-empty file charges quota (dedup shares with Drive blobs).
            assert project.used_bytes > 0
            # File contents are readable from the snapshot.
            entry, data = await svc.read_file(s, ctx, project_id=project.id, path="main.py")
            assert b"hello, sherpa" in data
            assert entry.entry_kind == "file"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_duplicate_name_conflicts_and_unknown_template_invalid() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            await svc.create_project(s, ctx, name="Dup")
            with pytest.raises(Conflict):
                await svc.create_project(s, ctx, name="Dup")
            with pytest.raises(Invalid):
                await svc.create_project(s, ctx, name="X", template_id="nope")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_snapshot_dir_synthesis_and_dedup() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await svc.create_project(s, ctx, name="Dirs")
            entries = [
                ArchiveEntry(path="a/b/c.txt", entry_kind="file", data=b"same"),
                ArchiveEntry(path="a/d.txt", entry_kind="file", data=b"same"),  # dedup
            ]
            snap = await svc.build_import_snapshot(s, ctx, project, entries)
            tree = await svc.get_tree(s, ctx, project_id=project.id)
            paths = {e.path: e.entry_kind for e in tree.entries}
            assert paths.get("a") == "dir"
            assert paths.get("a/b") == "dir"
            assert paths.get("a/b/c.txt") == "file"
            assert paths.get("a/d.txt") == "file"
            # Same bytes → charged once.
            assert snap.size_bytes == len(b"same")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_open_in_chat_binds_and_context_immutable() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await svc.create_project(s, ctx, name="Chatty")
            session = await svc.open_in_chat(s, ctx, project_id=project.id, title="hi")
            assert session.project_id == project.id

            pc = await svc.project_context(s, ctx, session_id=session.id)
            assert pc.project_id == project.id
            assert pc.project_name == "Chatty"
            assert pc.bound is False  # no admitted message yet

            # After the first admitted message the binding is reported immutable.
            bound_session = await s.get(SessionModel, (ctx.tenant_id, session.id))
            assert bound_session is not None
            bound_session.admitted_seq = 1
            await s.flush()
            pc2 = await svc.project_context(s, ctx, session_id=session.id)
            assert pc2.bound is True
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_cross_user_isolation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx_a = await _seed(s)
            project = await svc.create_project(s, ctx_a, name="Owned")
            # A different owner (separate tenant) cannot read it (ADR-015 isolation).
            ctx_b = await _seed(s)
            with pytest.raises(NotFound):
                await svc.get_project(s, ctx_b, project_id=project.id)
        finally:
            await s.rollback()
