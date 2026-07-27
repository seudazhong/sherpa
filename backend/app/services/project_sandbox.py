"""Workspace Projects W3 sandbox orchestration (ADR-040 + ADR-039).

Ties the durable working copy (:mod:`app.services.project_workcopy`) to the one-time
scratch mechanics (:mod:`app.sandbox.project_sandbox`). One ``project_run`` boundary:

1. acquire the working copy's single-writer lease → a fresh ``fence_token``;
2. record a ``project_sandbox_runs`` row (``state='materializing'``);
3. materialize ``base snapshot + persisted overlay`` into a FRESH disposable scratch tree
   (only project bytes — never a credential/snapshot/blob store/other Project/Drive/socket);
4. apply host-side edits, then run the command in the hardened, network-disabled container
   with **only** the scratch bind-mounted read-write (ADR-039);
5. compute the scratch delta (bounded); **persist** it into the overlay fence-guarded +
   idempotent (the only durable effect; events §2.11 ②) and set ``persisted_boundary_at``;
6. tear the scratch tree down (rebuildable cache — never recovery truth).

Named termination reasons only (events §2.11 ④). The sandbox has **no external side
effect** ⇒ **no** ``effect_unknown``: a lost container/node simply rematerializes from the
last persisted boundary. The caller owns the transaction (services flush, never commit).
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.files import build_object_store
from app.models import ProjectSandboxRun, ProjectWorkingCopy, StorageBlob
from app.sandbox import project_sandbox as psbx
from app.services import drive as drive_svc
from app.services import project_changes as changes_svc
from app.services import project_workcopy as wc_svc
from app.services.context import CallerContext
from app.services.errors import Conflict

_LIVE_STATES = ("open", "ready_for_review")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclasses.dataclass(frozen=True)
class SandboxRequest:
    """A ``project_run`` boundary: host-side edits applied to scratch, then an optional
    shell command (run/test) executed in the hardened container."""

    edits: list[psbx.ScratchEdit] = dataclasses.field(default_factory=list)
    command: str | None = None


@dataclasses.dataclass(frozen=True)
class SandboxOutcome:
    sandbox_run: ProjectSandboxRun
    stdout: str
    stderr: str
    change_set_id: uuid.UUID | None = None


def _materialize_entries(
    effective: dict[str, wc_svc.EffectiveEntry],
) -> list[psbx.MaterializeEntry]:
    return [
        psbx.MaterializeEntry(
            path=e.path,
            entry_kind=e.entry_kind,
            content_hash=e.content_hash,
            size_bytes=e.size_bytes,
            executable=e.executable,
            symlink_target=e.symlink_target,
        )
        for e in effective.values()
    ]


async def run_sandbox(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    run_id: uuid.UUID,
    request: SandboxRequest,
) -> SandboxOutcome:
    """Execute one bounded sandbox boundary against the working copy's one-time scratch copy
    and durably persist the resulting overlay. Records a ``project_sandbox_runs`` row with a
    named ``termination_reason``; a stale fence or over-bound delta is a safe named exit that
    persists nothing."""
    if wc.state not in _LIVE_STATES:
        raise Conflict("working copy is not open")

    fence = await wc_svc.acquire_lease(db, wc, owner=f"run:{run_id}")
    sr = ProjectSandboxRun(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=wc.project_id,
        working_copy_id=wc.id,
        session_id=wc.session_id,
        run_id=run_id,
        user_id=wc.user_id,
        base_snapshot_id=wc.base_snapshot_id,
        fence_token=fence,
        state="materializing",
    )
    db.add(sr)
    await db.flush()
    run_key = str(sr.id)
    sr.scratch_ref = str(psbx.scratch_dir_for(run_key))

    async def _read_blob(content_hash: bytes) -> bytes:
        blob = await db.get(StorageBlob, (ctx.tenant_id, wc.user_id, content_hash))
        if blob is None:
            raise psbx.ScratchError("blob_missing")
        return await build_object_store().get(blob.object_key)

    stdout = stderr = ""
    try:
        effective = await wc_svc.effective_tree(db, ctx, wc)
        try:
            manifest = await psbx.materialize(run_key, _materialize_entries(effective), _read_blob)
            for edit in request.edits:
                psbx.apply_edit(run_key, edit)
        except psbx.ScratchError as exc:
            sr.state = "failed"
            sr.termination_reason = exc.code
            await db.flush()
            return SandboxOutcome(sandbox_run=sr, stdout="", stderr="")

        sr.state = "running"
        await db.flush()

        reason = "done"
        exit_code: int | None = None
        timed_out = False
        if request.command:
            res = await psbx.run_in_scratch(run_key, request.command)
            stdout, stderr = res.stdout, res.stderr
            exit_code = res.exit_code
            timed_out = res.timed_out
            if res.error in ("sandbox_disabled",) or (res.error and "unavailable" in res.error):
                reason = "sandbox_unavailable"
            elif res.error:
                reason = "sandbox_unavailable"
            elif timed_out:
                reason = "wall_timeout"
            elif exit_code == 127:
                # shell "command not found" ⇒ a missing runtime/dependency: an explicit,
                # named outcome — the offline sandbox never silently installs packages.
                reason = "environment_missing_dependencies"

        # Compute the bounded delta of scratch vs the materialized base.
        try:
            delta = psbx.compute_delta(run_key, manifest)
        except psbx.ScratchError as exc:
            sr.state = "failed"
            sr.termination_reason = exc.code
            sr.exit_code = exit_code
            sr.timed_out = timed_out
            await db.flush()
            return SandboxOutcome(sandbox_run=sr, stdout=stdout, stderr=stderr)

        if delta.over_bounds:
            # Persist NOTHING on an over-bound delta (never a silent partial overlay); the
            # bounded/truncated review projection is W3.3.
            sr.state = "failed"
            sr.termination_reason = "changeset_bounds"
            sr.exit_code = exit_code
            sr.timed_out = timed_out
            await db.flush()
            return SandboxOutcome(sandbox_run=sr, stdout=stdout, stderr=stderr)

        # Stage changed blobs (content-addressed dedup) + build the overlay deltas.
        overlay: list[wc_svc.OverlayDelta] = []
        for d in delta.entries:
            if d.change_kind in ("added", "modified"):
                h, _ = await drive_svc.ensure_blob(
                    db, ctx, wc.user_id, data=d.data or b"", content_type="application/octet-stream"
                )
                overlay.append(
                    wc_svc.OverlayDelta(
                        path=d.path,
                        change_kind=d.change_kind,
                        entry_kind="file",
                        content_hash=h,
                        size_bytes=d.size_bytes,
                        executable=d.executable,
                    )
                )
            else:
                overlay.append(
                    wc_svc.OverlayDelta(path=d.path, change_kind="deleted", entry_kind="file")
                )

        published = True
        if overlay:
            published = await wc_svc.persist_overlay(
                db, ctx, wc, fence_token=fence, deltas=overlay, run_id=run_id
            )

        if not published:
            sr.state = "failed"
            sr.termination_reason = "fence_lost"
        elif reason == "wall_timeout":
            sr.state = "timed_out"
            sr.persisted_boundary_at = _now()
        else:
            sr.state = "persisted"
            sr.persisted_boundary_at = _now()
        sr.exit_code = exit_code
        sr.timed_out = timed_out
        sr.termination_reason = reason if published else "fence_lost"
        await db.flush()

        change_set_id: uuid.UUID | None = None
        if published:
            # Record the command output as an ephemeral artifact (charges no quota until
            # Keep/Export) + (re)build the reviewable change set from the overlay.
            if request.command and (stdout or stderr):
                combined = (stdout + ("\n" + stderr if stderr else "")).encode("utf-8", "replace")
                await changes_svc.record_artifact(
                    db, ctx, wc, run_id=run_id, name="run-output.log", data=combined
                )
            cs = await changes_svc.build_change_set(db, ctx, wc, run_id=run_id)
            change_set_id = cs.id if cs is not None else None
        return SandboxOutcome(
            sandbox_run=sr, stdout=stdout, stderr=stderr, change_set_id=change_set_id
        )
    finally:
        # The scratch tree is a rebuildable cache — always torn down; the durable boundary
        # is the persisted overlay, never the container/scratch.
        psbx.cleanup(run_key)
        sr.scratch_ref = None
        sr.container_ref = None


def sweep_orphan_scratch() -> int:
    """Startup sweep of scratch trees left by crashed runs (rebuildable cache)."""
    return psbx.sweep_orphans()
