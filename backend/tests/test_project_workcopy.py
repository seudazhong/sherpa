"""Workspace Projects W3 task-working-copy lifecycle (ADR-040 + ADR-039).

Durable, docker-free half of W3: lazy open + isolation, single-writer lease/fence,
fence-guarded overlay persist + quota reservation, Save compare-and-set head advance
(+ stale-head conflict), Save-selected subset, Discard, idle expiry.

Integration test — skips without a database (needs migration 0030). Uses the in-memory
object store; rolls back.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import Project, Tenant, User
from app.models import Session as SessionModel
from app.services import drive as drive_svc
from app.services import project_workcopy as wc_svc
from app.services import projects as projects_svc
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound
from app.services.project_workcopy import OverlayDelta


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


async def _project_with_head(s, ctx: CallerContext) -> Project:  # type: ignore[no-untyped-def]
    return await projects_svc.create_project(s, ctx, name="P", template_id="python-basic")


async def _bound_session(s, ctx: CallerContext, project: Project) -> SessionModel:  # type: ignore[no-untyped-def]
    sid = uuid.uuid4()
    session = SessionModel(
        tenant_id=ctx.tenant_id,
        id=sid,
        user_id=ctx.user_id,
        umo_key=f"web:chat:{sid}",
        channel="web",
        channel_installation_id="local",
        scope_type="chat",
        external_scope_id=str(sid),
        status="open",
        project_id=project.id,
        admitted_seq=1,
    )
    s.add(session)
    await s.flush()
    return session


async def _blob(s, ctx: CallerContext, data: bytes) -> tuple[bytes, int]:  # type: ignore[no-untyped-def]
    h, _ = await drive_svc.ensure_blob(s, ctx, ctx.user_id, data=data, content_type="text/plain")
    return h, len(data)


@pytest.mark.asyncio
async def test_open_is_lazy_idempotent_and_isolated() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            s1 = await _bound_session(s, ctx, project)
            s2 = await _bound_session(s, ctx, project)

            wc1 = await wc_svc.open_working_copy(s, ctx, session_id=s1.id)
            wc1b = await wc_svc.open_working_copy(s, ctx, session_id=s1.id)
            assert wc1.id == wc1b.id  # idempotent lazy open
            assert wc1.base_snapshot_id == project.current_snapshot_id
            assert wc1.base_head_generation == project.head_generation

            wc2 = await wc_svc.open_working_copy(s, ctx, session_id=s2.id)
            assert wc2.id != wc1.id  # isolated per session
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_open_requires_project_bound_session_with_head() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            # A General chat (no project_id) has no working copy.
            sid = uuid.uuid4()
            general = SessionModel(
                tenant_id=ctx.tenant_id,
                id=sid,
                user_id=ctx.user_id,
                umo_key=f"web:chat:{sid}",
                channel="web",
                channel_installation_id="local",
                scope_type="chat",
                external_scope_id=str(sid),
                status="open",
            )
            s.add(general)
            await s.flush()
            with pytest.raises(Invalid):
                await wc_svc.open_working_copy(s, ctx, session_id=sid)
            with pytest.raises(NotFound):
                await wc_svc.open_working_copy(s, ctx, session_id=uuid.uuid4())
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_stale_fence_cannot_publish() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            session = await _bound_session(s, ctx, project)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)

            stale = await wc_svc.acquire_lease(s, wc, owner="run-1")  # fence 1
            fresh = await wc_svc.acquire_lease(s, wc, owner="run-2")  # fence 2 (writer moved on)
            assert fresh > stale

            h, size = await _blob(s, ctx, b"print('x')\n")
            ok_stale = await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=stale,
                deltas=[
                    OverlayDelta(
                        path="new.py", change_kind="added", content_hash=h, size_bytes=size
                    )
                ],
            )
            assert ok_stale is False  # stale fence rejected — no publish
            assert wc.overlay_entry_count == 0

            ok_fresh = await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=fresh,
                deltas=[
                    OverlayDelta(
                        path="new.py", change_kind="added", content_hash=h, size_bytes=size
                    )
                ],
            )
            assert ok_fresh is True
            assert wc.overlay_entry_count == 1
            assert wc.state == "ready_for_review"
            # The new pending blob is reserved (uncounted until save).
            acct = await drive_svc.get_account(s, ctx, ctx.user_id)
            assert acct.reserved_bytes == size
            assert wc.reserved_bytes == size
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_save_advances_head_and_releases_reservation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            base_snap = project.current_snapshot_id
            base_gen = project.head_generation
            session = await _bound_session(s, ctx, project)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)
            fence = await wc_svc.acquire_lease(s, wc, owner="run-1")

            h, size = await _blob(s, ctx, b"# added by sandbox\n")
            await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=fence,
                deltas=[
                    OverlayDelta(
                        path="added.md", change_kind="added", content_hash=h, size_bytes=size
                    )
                ],
            )

            result = await wc_svc.save(s, ctx, wc)
            assert result.working_copy_state == "saved"
            assert project.current_snapshot_id != base_snap
            assert project.head_generation == base_gen + 1  # CAS token advanced
            assert result.snapshot.reason == "save"

            # The saved file is in the new head; reservation released; bytes now counted.
            entry, data = await projects_svc.read_file(
                s, ctx, project_id=project.id, path="added.md"
            )
            assert data == b"# added by sandbox\n"
            acct = await drive_svc.get_account(s, ctx, ctx.user_id)
            assert acct.reserved_bytes == 0
            assert wc.reserved_bytes == 0
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_save_conflicts_when_head_moved() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            session = await _bound_session(s, ctx, project)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)
            fence = await wc_svc.acquire_lease(s, wc, owner="run-1")
            h, size = await _blob(s, ctx, b"data\n")
            await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=fence,
                deltas=[
                    OverlayDelta(path="f.txt", change_kind="added", content_hash=h, size_bytes=size)
                ],
            )

            # Simulate another chat advancing the head (CAS token moves).
            project.head_generation += 1
            await s.flush()

            assert wc_svc.head_moved(project, wc) is True
            with pytest.raises(Conflict) as ei:
                await wc_svc.save(s, ctx, wc)
            assert ei.value.message == "head_moved"
            assert wc.state == "conflicted"
            with pytest.raises(Conflict, match="working_copy_conflicted"):
                await wc_svc.open_working_copy(s, ctx, session_id=session.id)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_save_selected_subset_rebases_remaining() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            session = await _bound_session(s, ctx, project)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)
            fence = await wc_svc.acquire_lease(s, wc, owner="run-1")

            ha, sa = await _blob(s, ctx, b"aaa\n")
            hb, sb = await _blob(s, ctx, b"bbb\n")
            await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=fence,
                deltas=[
                    OverlayDelta(path="a.txt", change_kind="added", content_hash=ha, size_bytes=sa),
                    OverlayDelta(path="b.txt", change_kind="added", content_hash=hb, size_bytes=sb),
                ],
            )

            result = await wc_svc.save(s, ctx, wc, selected_paths=["a.txt"])
            assert result.applied_paths == ["a.txt"]
            # a.txt is saved into the head; b.txt remains pending, rebased onto the new head.
            assert wc.state == "ready_for_review"
            assert wc.base_snapshot_id == project.current_snapshot_id
            assert wc.base_head_generation == project.head_generation
            assert wc.overlay_entry_count == 1
            entry, data = await projects_svc.read_file(s, ctx, project_id=project.id, path="a.txt")
            assert data == b"aaa\n"
            with pytest.raises(NotFound):
                await projects_svc.read_file(s, ctx, project_id=project.id, path="b.txt")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_discard_leaves_head_unchanged_and_releases_reservation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            base_snap = project.current_snapshot_id
            base_gen = project.head_generation
            session = await _bound_session(s, ctx, project)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)
            fence = await wc_svc.acquire_lease(s, wc, owner="run-1")
            h, size = await _blob(s, ctx, b"scratch\n")
            await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=fence,
                deltas=[
                    OverlayDelta(
                        path="tmp.txt", change_kind="added", content_hash=h, size_bytes=size
                    )
                ],
            )
            assert (await drive_svc.get_account(s, ctx, ctx.user_id)).reserved_bytes == size

            await wc_svc.discard(s, ctx, wc)
            assert wc.state == "discarded"
            assert wc.overlay_entry_count == 0
            # Head byte-identical to base; reservation released.
            assert project.current_snapshot_id == base_snap
            assert project.head_generation == base_gen
            assert (await drive_svc.get_account(s, ctx, ctx.user_id)).reserved_bytes == 0
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_idle_expiry_releases_reservation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project = await _project_with_head(s, ctx)
            session = await _bound_session(s, ctx, project)
            wc = await wc_svc.open_working_copy(s, ctx, session_id=session.id)
            fence = await wc_svc.acquire_lease(s, wc, owner="run-1")
            h, size = await _blob(s, ctx, b"idle\n")
            await wc_svc.persist_overlay(
                s,
                ctx,
                wc,
                fence_token=fence,
                deltas=[
                    OverlayDelta(
                        path="idle.txt", change_kind="added", content_hash=h, size_bytes=size
                    )
                ],
            )
            # Force the working copy past its idle TTL.
            wc.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
            await s.flush()

            n = await wc_svc.expire_idle(s)
            assert n >= 1
            assert wc.state == "expired"
            assert (await drive_svc.get_account(s, ctx, ctx.user_id)).reserved_bytes == 0
        finally:
            await s.rollback()
