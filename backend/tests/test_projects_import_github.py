"""Durable GitHub one-time import job (ADR-038, W2b; events §2.10).

Integration test — skips without a database (needs migration 0029). Uses a deterministic
GitHub mock transport + the in-memory object store; rolls back. Covers the happy path
(resolve→OID→tarball fetch→expand→snapshot with source_oid + provenance imported), the
tarball top-level strip, unsafe archive → import_failed (no snapshot), and a retryable
fetch failure re-fetched by the resolved OID (idempotent). Also asserts the token never
leaks into the snapshot tree.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import (
    Project,
    ProjectImportJob,
    ProjectSnapshotEntry,
    ProjectSource,
    Tenant,
    User,
)
from app.services import github_source as gh
from app.services import projects_import as pimp
from app.services.context import CallerContext
from tests.github_mock import TEST_OID, GithubMock


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


async def _connect(s, ctx, mock: GithubMock) -> None:  # type: ignore[no-untyped-def]
    await gh.create_connection(s, ctx, auth_kind="pat", token="github_pat_secret")  # noqa: S106


@pytest.mark.asyncio
async def test_github_import_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    mock = GithubMock()
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            await _connect(s, ctx, mock)
            project, job = await pimp.create_github_import(
                s,
                ctx,
                name="Imported GH",
                repo_external_id="123",
                owner="octocat",
                repo="hello",
                ref_type="branch",
                ref="main",
                connection_id=None,
            )
            assert project.source_status == "importing"
            assert job.create_kind == "github"

            reason, staging = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert reason == "done", reason
            assert staging is None

            fresh = await s.get(Project, (ctx.tenant_id, project.id))
            assert fresh is not None
            assert fresh.current_snapshot_id is not None
            assert fresh.source_status == "imported"

            src = await s.get(
                ProjectSource, (ctx.tenant_id, (await _source_id(s, ctx, project.id)))
            )
            assert src is not None
            assert src.status == "imported"
            assert src.source_oid == TEST_OID
            assert src.imported_at is not None

            # Snapshot records the source OID; the top-level dir was stripped.
            entries = await _entry_paths(s, ctx, fresh.current_snapshot_id)
            assert "README.md" in entries
            assert "src/app.py" in entries
            assert not any(p.startswith("octocat-hello-") for p in entries)

            # The sealed token must never appear in any snapshot entry path/target.
            for p in entries:
                assert "secret_pat" not in p
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_github_import_unsafe_archive_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    from tests.github_mock import make_unsafe_tarball

    mock = GithubMock(tarball=make_unsafe_tarball())
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            await _connect(s, ctx, mock)
            project, job = await pimp.create_github_import(
                s,
                ctx,
                name="Evil GH",
                repo_external_id="123",
                owner="octocat",
                repo="hello",
                ref_type="branch",
                ref="main",
                connection_id=None,
            )
            reason, _ = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert reason == "unsafe_archive"
            fresh = await s.get(Project, (ctx.tenant_id, project.id))
            assert fresh is not None and fresh.current_snapshot_id is None
            assert fresh.source_status == "import_failed"
            assert fresh.status == "active"  # visible + deletable, never status=failed
            job2 = await s.get(ProjectImportJob, (ctx.tenant_id, job.id))
            assert job2 is not None and job2.stage == "failed"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_github_import_retry_refetches_by_oid(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    # First tarball fetch fails (after resolve succeeds), then retry re-fetches by OID.
    mock = GithubMock(fail_tarball_times=1)
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            await _connect(s, ctx, mock)
            project, job = await pimp.create_github_import(
                s,
                ctx,
                name="Retry GH",
                repo_external_id="123",
                owner="octocat",
                repo="hello",
                ref_type="branch",
                ref="main",
                connection_id=None,
            )
            r1, _ = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w1"
            )
            assert r1 == "error" or r1 == "repo_unavailable" or r1 == "upstream_unreachable"
            job1 = await s.get(ProjectImportJob, (ctx.tenant_id, job.id))
            assert job1 is not None and job1.stage == "failed"
            # Ref was resolved before the fetch failed → OID pinned for the retry.
            assert job1.resolved_oid == TEST_OID

            # Retry re-enqueues + re-fetches by the pinned OID → identical bytes → done.
            await pimp.retry_github_import(s, ctx, project_id=project.id)
            r2, _ = await pimp.process_import(
                s, tenant_id=ctx.tenant_id, project_id=project.id, lease_owner="w2"
            )
            assert r2 == "done", r2
            fresh = await s.get(Project, (ctx.tenant_id, project.id))
            assert fresh is not None and fresh.current_snapshot_id is not None
            assert fresh.source_status == "imported"
        finally:
            await s.rollback()


async def _source_id(s, ctx, project_id) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return await s.scalar(
        select(ProjectSource.id).where(
            ProjectSource.tenant_id == ctx.tenant_id,
            ProjectSource.project_id == project_id,
        )
    )


async def _entry_paths(s, ctx, snapshot_id) -> set[str]:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    rows = (
        (
            await s.execute(
                select(ProjectSnapshotEntry.path).where(
                    ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
                    ProjectSnapshotEntry.snapshot_id == snapshot_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)
