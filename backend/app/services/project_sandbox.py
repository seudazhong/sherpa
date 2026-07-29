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

Named termination reasons only (events §2.11 ④): every failing exit emits **one** structured
worker log line (with the bounded raw detail, for the operator) and **one** redacted
observation for the model — never a blanket ``sandbox_unavailable`` with no log. The sandbox
has **no external side effect** ⇒ **no** ``effect_unknown``: a lost container/node simply
rematerializes from the last persisted boundary. The caller owns the transaction (services
flush, never commit).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.files import build_object_store
from app.models import ProjectSandboxRun, ProjectWorkingCopy, StorageBlob
from app.sandbox import project_sandbox as psbx
from app.sandbox import runner
from app.services import drive as drive_svc
from app.services import project_changes as changes_svc
from app.services import project_workcopy as wc_svc
from app.services.context import CallerContext
from app.services.errors import Conflict

logger = logging.getLogger("app.services.project_sandbox")

_LIVE_STATES = ("open", "ready_for_review")

#: One redacted, model-facing sentence per named exit (events §2.11 ④). These sentences are
#: static: they name the reason and stay actionable, but they never carry the raw failure
#: text, a host path, an image reference or a credential (ADR-019) — that detail goes to the
#: structured worker log only. The runtime-level half is shared with :mod:`app.sandbox.runner`.
FAILURE_NOTES: dict[str, str] = {
    **runner.RUNTIME_FAILURE_NOTES,
    "wall_timeout": "the command exceeded the sandbox wall-clock limit and was killed",
    "environment_missing_dependencies": (
        "the command is not available in the sandbox image; the offline sandbox never installs "
        "packages or reaches the network"
    ),
    "changeset_bounds": (
        "the resulting change set exceeded the configured file/byte bounds, so nothing was "
        "persisted"
    ),
    "path_escape": (
        "a requested path resolved outside the disposable scratch tree and was rejected"
    ),
    "fence_lost": (
        "another writer advanced this working copy, so this boundary was rejected and nothing "
        "was persisted"
    ),
    "scratch_too_large": "the materialized project exceeded the scratch size bound",
    "blob_missing": "a project file's stored bytes could not be read",
    "bad_edit_op": "an unsupported scratch edit operation was requested",
}


def failure_note(reason: str) -> str:
    """The redacted observation for a named exit — safe to hand to the model verbatim."""
    return f"{reason}: {FAILURE_NOTES.get(reason, runner.UNMODELLED_NOTE)}"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _observe_failure(
    sr: ProjectSandboxRun,
    reason: str,
    *,
    detail: str | None = None,
    had_command: bool = False,
) -> str:
    """Emit **one** structured worker log line for a failing exit and return the **one**
    redacted observation for the model (events §2.11 ④). The raw ``detail`` stays in the
    operator log; it never reaches the model, the change set or the journal."""
    logger.warning(
        "project sandbox run failed",
        extra={
            "termination_reason": reason,
            "sandbox_run_id": str(sr.id),
            "project_id": str(sr.project_id),
            "working_copy_id": str(sr.working_copy_id),
            "run_id": str(sr.run_id),
            "sandbox_state": sr.state,
            "exit_code": sr.exit_code,
            "sandbox_kind": settings.sandbox_kind,
            "sandbox_image": settings.sandbox_image,
            "had_command": had_command,
            "sandbox_error_detail": detail,
        },
    )
    return failure_note(reason)


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
    #: The redacted observation for a failing exit (``None`` on ``done``). Callers surface it
    #: to the model / user verbatim; it never carries raw failure text.
    failure_note: str | None = None


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
    persists nothing. Every failing exit is logged once and returns a redacted
    ``failure_note`` — the caller hands it to the model as an observation."""
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
            note = _observe_failure(sr, exc.code, had_command=bool(request.command))
            return SandboxOutcome(sandbox_run=sr, stdout="", stderr="", failure_note=note)

        sr.state = "running"
        await db.flush()

        reason = "done"
        detail: str | None = None
        exit_code: int | None = None
        timed_out = False
        if request.command:
            res = await psbx.run_in_scratch(run_key, request.command)
            stdout, stderr = res.stdout, res.stderr
            exit_code = res.exit_code
            timed_out = res.timed_out
            if res.error:
                # Each runtime failure keeps its OWN contract name (events §2.11 ④); the
                # blanket ``sandbox_unavailable`` collapse of backlog B-8 is gone.
                reason = res.error
                detail = res.error_detail
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
            note = _observe_failure(sr, exc.code, had_command=bool(request.command))
            return SandboxOutcome(sandbox_run=sr, stdout=stdout, stderr=stderr, failure_note=note)

        if delta.over_bounds:
            # Persist NOTHING on an over-bound delta (never a silent partial overlay); the
            # bounded/truncated review projection is W3.3.
            sr.state = "failed"
            sr.termination_reason = "changeset_bounds"
            sr.exit_code = exit_code
            sr.timed_out = timed_out
            await db.flush()
            note = _observe_failure(sr, "changeset_bounds", had_command=bool(request.command))
            return SandboxOutcome(sandbox_run=sr, stdout=stdout, stderr=stderr, failure_note=note)

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

        final_reason = reason if published else "fence_lost"
        if not published:
            sr.state = "failed"
        elif reason == "wall_timeout":
            sr.state = "timed_out"
            sr.persisted_boundary_at = _now()
        else:
            sr.state = "persisted"
            sr.persisted_boundary_at = _now()
        sr.exit_code = exit_code
        sr.timed_out = timed_out
        sr.termination_reason = final_reason
        await db.flush()

        # One log line + one redacted observation per FAILING exit; a clean `done` stays
        # silent. Note that a runtime failure still persists the host-side edits above —
        # the error is an observation for the model, never a crash (docs/04 invariant).
        settled_note: str | None = None
        if final_reason != "done":
            settled_note = _observe_failure(
                sr,
                final_reason,
                detail=detail if published else None,
                had_command=bool(request.command),
            )

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
            sandbox_run=sr,
            stdout=stdout,
            stderr=stderr,
            change_set_id=change_set_id,
            failure_note=settled_note,
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
