"""Durable Project archive-import job (ADR-037, W2a; events §2.9).

Integration test — skips without a database (needs migration 0028). Uses the in-memory
object store; rolls back. Covers the happy path (staged → activate), unsafe archive →
failed-no-snapshot, idempotent re-run, and recovery listing.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from app.db import SessionLocal, ping_db
from app.models import Project, ProjectImportJob, Tenant, User
from app.services import projects_import as pimp
from app.services.context import CallerContext


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_archive_import_happy_path_activates() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            raw = _zip([("README.md", b"# archived"), ("src/app.py", b"print('x')")])
            project, job = await pimp.create_archive_import(
                s, ctx, name="Imported", archive_bytes=raw
            )
            assert project.current_snapshot_id is None
            assert job.stage == "queued"

            reason, staging = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert reason == "done"
            fresh = await s.get(Project, (ctx.tenant_id, project.id))
            assert fresh is not None
            assert fresh.current_snapshot_id is not None
            assert fresh.used_bytes > 0
            job2 = await s.get(ProjectImportJob, (ctx.tenant_id, job.id))
            assert job2 is not None and job2.stage == "done"
            assert job2.entry_count and job2.entry_count >= 2
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_unsafe_archive_fails_without_snapshot() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            raw = _zip([("../escape.txt", b"pwn")])
            project, job = await pimp.create_archive_import(s, ctx, name="Evil", archive_bytes=raw)
            reason, _ = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert reason == "unsafe_archive"
            fresh = await s.get(Project, (ctx.tenant_id, project.id))
            assert fresh is not None and fresh.current_snapshot_id is None
            job2 = await s.get(ProjectImportJob, (ctx.tenant_id, job.id))
            assert job2 is not None and job2.stage == "failed"
            assert job2.termination_reason == "unsafe_archive"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_import_is_idempotent_on_rerun() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            raw = _zip([("a.txt", b"1")])
            project, _job = await pimp.create_archive_import(s, ctx, name="Idem", archive_bytes=raw)
            r1, _ = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert r1 == "done"
            head = (await s.get(Project, (ctx.tenant_id, project.id))).current_snapshot_id  # type: ignore[union-attr]
            r2, _ = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert r2 in ("already_done", "done")
            head2 = (await s.get(Project, (ctx.tenant_id, project.id))).current_snapshot_id  # type: ignore[union-attr]
            assert head == head2  # no new snapshot on re-run
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_recover_lists_queued_jobs() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            raw = _zip([("a.txt", b"1")])
            project, _ = await pimp.create_archive_import(s, ctx, name="Stuck", archive_bytes=raw)
            await s.flush()
            pending = await pimp.recover_stuck_imports(s)
            assert (ctx.tenant_id, project.id) in pending
        finally:
            await s.rollback()
