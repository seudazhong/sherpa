"""Workspace Projects W3 change-set projection + review actions (ADR-040).

Change-set build from a sandbox boundary (added/modified/deleted + bounded diffs),
Save-selected apply (head-generation CAS + partial-save rebase), stale-head conflict,
Discard, artifacts (ephemeral → Keep charges quota / Export copies to Drive), and the
agent tool policy (project_run allow, project_review_changes allow; Save is user-only).

Integration test — skips without a database (needs migration 0030). In-memory object
store; rolls back. Uses a temp scratch root.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import Tenant, User
from app.permissions.policy import evaluate
from app.sandbox.runtime import ScratchEdit
from app.services import drive as drive_svc
from app.services import project_changes as changes_svc
from app.services import project_sandbox as sbx_svc
from app.services import project_workcopy as wc_svc
from app.services import projects as projects_svc
from app.services.context import CallerContext
from app.services.errors import Conflict, TooLarge
from app.tools.project_tools import ProjectReviewChangesTool, ProjectRunTool


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


async def _open_wc(s, ctx):  # type: ignore[no-untyped-def]
    project = await projects_svc.create_project(s, ctx, name="P", template_id="python-basic")
    sid = uuid.uuid4()
    s.add(
        SessionModel(
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
    )
    await s.flush()
    wc = await wc_svc.open_working_copy(s, ctx, session_id=sid)
    return project, sid, wc


async def _run(s, ctx, wc, *, edits):  # type: ignore[no-untyped-def]
    return await sbx_svc.run_sandbox(
        s, ctx, wc, run_id=uuid.uuid4(), request=sbx_svc.SandboxRequest(edits=edits)
    )


def test_tool_policy() -> None:
    assert evaluate(ProjectRunTool()) == "allow"
    assert evaluate(ProjectReviewChangesTool()) == "allow"


@pytest.mark.asyncio
async def test_change_set_build_and_diff() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            _project, _sid, wc = await _open_wc(s, ctx)
            out = await _run(
                s,
                ctx,
                wc,
                edits=[
                    ScratchEdit(path="added.txt", op="write", data=b"new\n"),
                    ScratchEdit(path="README.md", op="write", data=b"# Python basic - edited\n"),
                    ScratchEdit(path="requirements.txt", op="delete"),
                ],
            )
            cs = await changes_svc.get_change_set(
                s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
            )
            assert (cs.added_count, cs.modified_count, cs.deleted_count) == (1, 1, 1)
            entries, _ = await changes_svc.get_change_set_entries(s, ctx, cs)
            by_path = {e.path: e for e in entries}
            assert by_path["README.md"].change_kind == "modified"
            assert by_path["README.md"].diff_object_key is not None
            # Bounded unified diff is retrievable.
            diff = await changes_svc.get_entry_diff(
                s, ctx, project_id=wc.project_id, cs_id=cs.id, entry_id=by_path["README.md"].id
            )
            assert "edited" in diff
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_apply_advances_head_and_marks_applied() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, _sid, wc = await _open_wc(s, ctx)
            base_gen = project.head_generation
            out = await _run(s, ctx, wc, edits=[ScratchEdit(path="x.txt", op="write", data=b"1\n")])
            result = await changes_svc.apply_change_set(
                s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
            )
            assert result.change_set.state == "applied"
            assert result.change_set.created_snapshot_id is not None
            assert project.head_generation == base_gen + 1
            entry, data = await projects_svc.read_file(s, ctx, project_id=project.id, path="x.txt")
            assert data == b"1\n"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_apply_selected_subset_rebuilds_remaining_change_set() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, _sid, wc = await _open_wc(s, ctx)
            out = await _run(
                s,
                ctx,
                wc,
                edits=[
                    ScratchEdit(path="a.txt", op="write", data=b"a\n"),
                    ScratchEdit(path="b.txt", op="write", data=b"b\n"),
                ],
            )
            cs = await changes_svc.get_change_set(
                s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
            )
            entries, _ = await changes_svc.get_change_set_entries(s, ctx, cs)
            a_id = next(e.id for e in entries if e.path == "a.txt")
            result = await changes_svc.apply_change_set(
                s, ctx, project_id=wc.project_id, cs_id=cs.id, selected_entry_ids=[a_id]
            )
            assert result.new_open_change_set_id is not None  # remainder rebuilt
            assert wc.state == "ready_for_review"
            entry, data = await projects_svc.read_file(s, ctx, project_id=project.id, path="a.txt")
            assert data == b"a\n"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_apply_conflicts_when_head_moved() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, _sid, wc = await _open_wc(s, ctx)
            out = await _run(s, ctx, wc, edits=[ScratchEdit(path="c.txt", op="write", data=b"c\n")])
            # Head advances underneath (another chat saved).
            project.head_generation += 1
            await s.flush()
            with pytest.raises(Conflict) as ei:
                await changes_svc.apply_change_set(
                    s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
                )
            assert ei.value.message == "head_moved"
            cs = await changes_svc.get_change_set(
                s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
            )
            assert cs.state == "conflicted"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_discard_change_set_leaves_head_unchanged() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, _sid, wc = await _open_wc(s, ctx)
            base_snap = project.current_snapshot_id
            out = await _run(s, ctx, wc, edits=[ScratchEdit(path="d.txt", op="write", data=b"d\n")])
            await changes_svc.discard_change_set(
                s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
            )
            assert wc.state == "discarded"
            assert project.current_snapshot_id == base_snap
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_artifact_keep_charges_quota_and_export_copies_to_drive() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            project, _sid, wc = await _open_wc(s, ctx)
            art = await changes_svc.record_artifact(
                s, ctx, wc, run_id=None, name="report.txt", data=b"1 passed\n"
            )
            # Ephemeral: no quota charged yet.
            used0 = (await drive_svc.get_account(s, ctx, ctx.user_id)).used_bytes
            kept = await changes_svc.keep_artifact(
                s, ctx, project_id=wc.project_id, artifact_id=art.id
            )
            assert kept.retention == "retained"
            used1 = (await drive_svc.get_account(s, ctx, ctx.user_id)).used_bytes
            assert used1 > used0  # retained artifact now counts
            node = await changes_svc.export_artifact(
                s, ctx, project_id=wc.project_id, artifact_id=art.id, name="exported.txt"
            )
            assert node.name == "exported.txt"
            assert node.size_bytes == len(b"1 passed\n")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_binary_entry_has_no_inline_diff() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            _project, _sid, wc = await _open_wc(s, ctx)
            out = await _run(
                s,
                ctx,
                wc,
                edits=[ScratchEdit(path="logo.bin", op="write", data=b"\x00\x01\x02BIN\x00")],
            )
            cs = await changes_svc.get_change_set(
                s, ctx, project_id=wc.project_id, cs_id=out.change_set_id
            )
            entries, _ = await changes_svc.get_change_set_entries(s, ctx, cs)
            bin_entry = next(e for e in entries if e.path == "logo.bin")
            assert bin_entry.is_binary is True
            assert bin_entry.diff_object_key is None
            with pytest.raises(TooLarge):
                await changes_svc.get_entry_diff(
                    s, ctx, project_id=wc.project_id, cs_id=cs.id, entry_id=bin_entry.id
                )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_the_orphan_gc_does_not_delete_change_set_diff_spills() -> None:
    """Regression for backlog B-12, found during Phase TR P3 human-lane verification.

    Change-set unified diffs are spilled to the object store under ``project-diff/`` and have
    no ``storage_blobs`` row, so the Drive orphan GC deleted them on its next cron tick. The
    entry row kept its ``diff_object_key`` while the object was gone, so every diff in Change
    Review rendered "(could not load diff)" and the API returned 500 NoSuchKey. Observed live:
    `cron:drive_maintenance ● 'gc=0 orphans=6'`, 26 seconds before the panel was opened.
    """
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            _project, _sid, wc = await _open_wc(s, ctx)
            await sbx_svc.run_sandbox(
                s,
                ctx,
                wc,
                run_id=uuid.uuid4(),
                request=sbx_svc.SandboxRequest(
                    edits=[ScratchEdit(path="calc.py", op="write", data=b"def add(a, b):\n")]
                ),
            )
            cs = await changes_svc.build_change_set(s, ctx, wc, run_id=uuid.uuid4())
            assert cs is not None
            entries, _ = await changes_svc.get_change_set_entries(s, ctx, cs)
            spilled = [e for e in entries if e.diff_object_key]
            assert spilled, "expected at least one spilled diff to protect"

            swept = await drive_svc.sweep_orphan_objects(s)

            # The GC may legitimately remove unrelated orphans; it must not touch these.
            for e in spilled:
                diff = await changes_svc.get_entry_diff(
                    s, ctx, project_id=wc.project_id, cs_id=cs.id, entry_id=e.id
                )
                assert "def add" in diff, f"diff spill was swept (removed={swept})"
        finally:
            await s.rollback()


async def _snapshot_entry(s, ctx, snapshot_id, path):  # type: ignore[no-untyped-def]
    """Read the persisted head-snapshot row directly — stronger than trusting a lazily
    loaded relationship, and it is the row the next materialize actually reads."""
    from sqlalchemy import select

    from app.models import ProjectSnapshotEntry

    row = (
        await s.execute(
            select(ProjectSnapshotEntry).where(
                ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
                ProjectSnapshotEntry.snapshot_id == snapshot_id,
                ProjectSnapshotEntry.path == path,
            )
        )
    ).scalar_one()
    return row


@pytest.mark.asyncio
async def test_the_executable_bit_survives_the_change_set_save_and_head_snapshot() -> None:
    """Regression cover for the Phase TR P3 review fix, end to end through the database.

    `chmod +x` with byte-identical content used to produce an **empty delta**, because the
    materialized baseline stored only a content hash. That was verified by hand in the
    browser (change-set `exec` badge, `executable=t` in the saved snapshot); this pins the
    same result automatically so it cannot silently regress.

    The whole chain is exercised, because each link could drop the bit independently:
    sandbox delta -> overlay entry -> change-set entry -> Save/CAS -> head snapshot row.
    """
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            _project, _sid, wc = await _open_wc(s, ctx)

            # 1. create the file NON-executable and save it, so the executable change in
            #    step 2 is measured against a committed head rather than a pending edit.
            body = b"#!/bin/sh\necho hi\n"
            await sbx_svc.run_sandbox(
                s,
                ctx,
                wc,
                run_id=uuid.uuid4(),
                request=sbx_svc.SandboxRequest(
                    edits=[ScratchEdit(path="run.sh", op="write", data=body, executable=False)]
                ),
            )
            await changes_svc.build_change_set(s, ctx, wc, run_id=uuid.uuid4())
            first = await wc_svc.save(s, ctx, wc)
            base_row = await _snapshot_entry(s, ctx, first.snapshot.id, "run.sh")
            assert base_row.executable is False
            base_hash = base_row.content_hash

            wc = await wc_svc.open_working_copy(s, ctx, session_id=wc.session_id)
            eff = await wc_svc.effective_tree(s, ctx, wc)
            assert eff["run.sh"].executable is False

            # 2. flip ONLY the executable bit — identical bytes.
            out = await sbx_svc.run_sandbox(
                s,
                ctx,
                wc,
                run_id=uuid.uuid4(),
                request=sbx_svc.SandboxRequest(
                    edits=[ScratchEdit(path="run.sh", op="write", data=body, executable=True)]
                ),
            )
            assert out.termination_reason == "done"

            # The overlay records it as a real change even though the content is unchanged.
            eff2 = await wc_svc.effective_tree(s, ctx, wc)
            assert eff2["run.sh"].executable is True
            assert eff2["run.sh"].content_hash == base_hash, "content must be untouched"

            # 3. it reaches the reviewable change set...
            cs = await changes_svc.build_change_set(s, ctx, wc, run_id=uuid.uuid4())
            assert cs is not None
            entries, _ = await changes_svc.get_change_set_entries(s, ctx, cs)
            entry = next(e for e in entries if e.path == "run.sh")
            assert entry.change_kind == "modified"
            assert entry.executable is True
            assert entry.old_content_hash == entry.new_content_hash

            # 4. ...and survives Save into the new head snapshot row.
            saved = await wc_svc.save(s, ctx, wc)
            head_row = await _snapshot_entry(s, ctx, saved.snapshot.id, "run.sh")
            assert head_row.executable is True
            assert head_row.content_hash == base_hash
        finally:
            await s.rollback()
