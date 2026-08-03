"""Workspace Projects W3 sandbox orchestration (ADR-040 + ADR-039 + ADR-047).

Ties the durable working copy (:mod:`app.services.project_workcopy`) to the one-time
disposable copy mechanics (:mod:`app.sandbox.runtime`). One ``project_run`` boundary:

1. acquire the working copy's single-writer lease → a fresh ``fence_token``;
2. open a ``project_runtime_sessions`` row (``scope='project'``, ``state='opening'``);
3. materialize ``base snapshot + persisted overlay`` into a FRESH in-memory disposable copy
   (only project bytes — never a credential/snapshot/blob store/other Project/Drive/socket);
4. apply host-side edits, then run the command in the hardened, network-disabled container
   which receives that copy **as a tar** (ADR-047: no bind mount, no host path), recording
   it as one ``project_exec_runs`` row;
5. compute the delta of what came back (bounded); **persist** it into the overlay
   fence-guarded + idempotent (the only durable effect; events §2.11 ②) and set
   ``persisted_boundary_at``;
6. drop the disposable copy (rebuildable cache — never recovery truth) and close the
   runtime session.

The session/exec split comes from ADR-047 + ADR-048, which replaced
``project_sandbox_runs``. Phase TR P1 moved the bookkeeping onto the target tables and
**P3 replaced the transport**: the container-create call passes no filesystem path at all,
which is what made backlog B-8 unfixable before. The remaining RuntimeSession product
semantics (``runtime_open`` → ``sh_exec``* → ``runtime_close`` spanning many commands,
async worker-executed REST, streaming/cancel) land in P4, and the human Run lane in P5.
Today one boundary still opens and closes exactly one session.

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
from app.models import ProjectExecRun, ProjectRuntimeSession, ProjectWorkingCopy, StorageBlob
from app.objectstore import build_object_store
from app.sandbox import runtime as sbx
from app.services import drive as drive_svc
from app.services import project_changes as changes_svc
from app.services import project_workcopy as wc_svc
from app.services.context import CallerContext
from app.services.errors import Conflict

logger = logging.getLogger("app.services.project_sandbox")

_LIVE_STATES = ("open", "ready_for_review")

#: ``project_exec_runs.command_preview`` is bounded — it is what the approval envelope and
#: the Change Review render, never an unbounded model-supplied string.
COMMAND_PREVIEW_MAX = 500

#: One redacted, model-facing sentence per named exit (events §2.11 ④). These sentences are
#: static: they name the reason and stay actionable, but they never carry the raw failure
#: text, a host path, an image reference or a credential (ADR-019) — that detail goes to the
#: structured worker log only. The runtime-level half is shared with :mod:`app.sandbox.runtime`.
FAILURE_NOTES: dict[str, str] = {
    **sbx.RUNTIME_FAILURE_NOTES,
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
        "a requested path resolved outside the disposable workspace copy and was rejected"
    ),
    "fence_lost": (
        "another writer advanced this working copy, so this boundary was rejected and nothing "
        "was persisted"
    ),
    "scratch_too_large": "the materialized project exceeded the sandbox transfer size bound",
    "credential_leak": (
        "a credential-shaped path reached the sandbox transfer boundary and the transfer was "
        "refused"
    ),
    "blob_missing": "a project file's stored bytes could not be read",
    "bad_edit_op": "an unsupported workspace edit operation was requested",
}


def failure_note(reason: str) -> str:
    """The redacted observation for a named exit — safe to hand to the model verbatim."""
    return f"{reason}: {FAILURE_NOTES.get(reason, sbx.UNMODELLED_NOTE)}"


#: Placeholder that replaces a delta entry once its bytes are durable, so the list stops
#: pinning buffers it no longer needs (config §1.7 peak model).
_DROPPED = sbx.DeltaEntry(path="", change_kind="deleted", data=None, size_bytes=0, executable=False)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _observe_failure(
    rs: ProjectRuntimeSession,
    reason: str,
    *,
    er: ProjectExecRun | None = None,
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
            "runtime_session_id": str(rs.id),
            "exec_run_id": str(er.id) if er is not None else None,
            "project_id": str(rs.project_id),
            "working_copy_id": str(rs.working_copy_id),
            "run_id": str(er.run_id) if er is not None else None,
            "runtime_state": rs.state,
            "exit_code": er.exit_code if er is not None else None,
            "sandbox_kind": settings.sandbox_kind,
            "sandbox_image": settings.sandbox_image,
            "had_command": had_command,
            "sandbox_error_detail": detail,
        },
    )
    return failure_note(reason)


@dataclasses.dataclass(frozen=True)
class SandboxRequest:
    """A ``project_run`` boundary: host-side edits applied to the disposable copy, then an
    optional shell command (run/test) executed in the hardened container."""

    edits: list[sbx.ScratchEdit] = dataclasses.field(default_factory=list)
    command: str | None = None


@dataclasses.dataclass(frozen=True)
class SandboxOutcome:
    #: The ``project_runtime_sessions`` row for this boundary (opened and closed by it).
    runtime_session: ProjectRuntimeSession
    stdout: str
    stderr: str
    #: The ``project_exec_runs`` row, present only when a command actually ran. An
    #: edits-only boundary executes nothing, so it records no exec run.
    exec_run: ProjectExecRun | None = None
    change_set_id: uuid.UUID | None = None
    #: The redacted observation for a failing exit (``None`` on ``done``). Callers surface it
    #: to the model / user verbatim; it never carries raw failure text.
    failure_note: str | None = None

    @property
    def termination_reason(self) -> str | None:
        """The boundary's named exit — the exec run's when a command ran, else the
        session's. Exactly one of them settles any given boundary."""
        if self.exec_run is not None:
            return self.exec_run.termination_reason
        return self.runtime_session.termination_reason


def _materialize_entries(
    effective: dict[str, wc_svc.EffectiveEntry],
) -> list[sbx.MaterializeEntry]:
    return [
        sbx.MaterializeEntry(
            path=e.path,
            entry_kind=e.entry_kind,
            content_hash=e.content_hash,
            size_bytes=e.size_bytes,
            executable=e.executable,
            symlink_target=e.symlink_target,
        )
        for e in effective.values()
    ]


def _fail(
    rs: ProjectRuntimeSession,
    er: ProjectExecRun | None,
    reason: str,
    *,
    exit_code: int | None,
    timed_out: bool,
) -> None:
    """Settle a FAILING boundary: nothing was durably persisted. The named reason lands on
    the exec run when a command ran, and on the session otherwise — never on both, so there
    is exactly one authoritative name per boundary."""
    rs.state = "failed"
    if er is None:
        rs.termination_reason = reason
        return
    er.state = "failed"
    er.termination_reason = reason
    er.exit_code = exit_code
    er.timed_out = timed_out


def _settle(
    rs: ProjectRuntimeSession,
    er: ProjectExecRun | None,
    reason: str,
    *,
    exit_code: int | None,
    timed_out: bool,
) -> None:
    """Settle a boundary whose overlay WAS persisted. ``persisted_boundary_at`` is the
    durability marker: an exec is not reported durably complete without it."""
    if er is None:
        rs.termination_reason = reason
        return
    er.state = "persisted"
    er.termination_reason = reason
    er.exit_code = exit_code
    er.timed_out = timed_out
    er.persisted_boundary_at = _now()


async def run_sandbox(
    db: AsyncSession,
    ctx: CallerContext,
    wc: ProjectWorkingCopy,
    *,
    run_id: uuid.UUID,
    request: SandboxRequest,
) -> SandboxOutcome:
    """Execute one bounded sandbox boundary against the working copy's one-time disposable
    copy and durably persist the resulting overlay. Opens one ``project_runtime_sessions``
    row (closed on every exit, so ``uq_prs_live`` stays satisfiable) and, when a command
    runs, one ``project_exec_runs`` row carrying the named ``termination_reason``; a stale
    fence or over-bound delta is a safe named exit that persists nothing. Every failing exit
    is logged once and returns a redacted ``failure_note`` — the caller hands it to the model
    as an observation."""
    if wc.state not in _LIVE_STATES:
        raise Conflict("working copy is not open")

    fence = await wc_svc.acquire_lease(db, wc, owner=f"run:{run_id}")
    rs = ProjectRuntimeSession(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=wc.project_id,
        working_copy_id=wc.id,
        session_id=wc.session_id,
        user_id=wc.user_id,
        scope="project",
        base_snapshot_id=wc.base_snapshot_id,
        fence_token=fence,
        state="opening",
        image=settings.sandbox_image,
    )
    db.add(rs)
    await db.flush()
    er: ProjectExecRun | None = None

    async def _read_blob(content_hash: bytes) -> bytes:
        blob = await db.get(StorageBlob, (ctx.tenant_id, wc.user_id, content_hash))
        if blob is None:
            raise sbx.ScratchError("blob_missing")
        return await build_object_store().get(blob.object_key)

    stdout = stderr = ""
    try:
        effective = await wc_svc.effective_tree(db, ctx, wc)
        try:
            ws = await sbx.materialize(_materialize_entries(effective), _read_blob)
            rs.entry_count = len(ws.base_manifest)
            for edit in request.edits:
                sbx.apply_edit(ws, edit)
        except sbx.ScratchError as exc:
            rs.state = "failed"
            rs.termination_reason = exc.code
            await db.flush()
            note = _observe_failure(rs, exc.code, had_command=bool(request.command))
            return SandboxOutcome(runtime_session=rs, stdout="", stderr="", failure_note=note)

        rs.state = "ready"
        await db.flush()

        reason = "done"
        detail: str | None = None
        exit_code: int | None = None
        timed_out = False
        # No command ⇒ nothing was executed, so the host-side copy IS the result tree.
        result_files = ws.files
        if request.command:
            er = ProjectExecRun(
                tenant_id=ctx.tenant_id,
                id=uuid.uuid4(),
                runtime_session_id=rs.id,
                run_id=run_id,
                seq=1,
                command_preview=request.command[:COMMAND_PREVIEW_MAX],
                state="running",
            )
            db.add(er)
            rs.state = "executing"
            await db.flush()

            started = _now()
            outcome = await sbx.run_workspace(ws, request.command, session_label=str(rs.id))
            res = outcome.result
            er.duration_ms = int((_now() - started).total_seconds() * 1000)
            stdout, stderr = res.stdout, res.stderr
            exit_code = res.exit_code
            timed_out = res.timed_out
            er.output_truncated = res.output_truncated
            rs.ingress_bytes = res.ingress_bytes
            if outcome.files is not None:
                result_files = outcome.files
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

        # Compute the bounded delta of the result tree vs the materialized base.
        try:
            delta = sbx.compute_delta(ws, result_files)
        except sbx.ScratchError as exc:
            _fail(rs, er, exc.code, exit_code=exit_code, timed_out=timed_out)
            await db.flush()
            note = _observe_failure(rs, exc.code, er=er, had_command=bool(request.command))
            return SandboxOutcome(
                runtime_session=rs, exec_run=er, stdout=stdout, stderr=stderr, failure_note=note
            )

        if delta.over_bounds:
            # Persist NOTHING on an over-bound delta (never a silent partial overlay); the
            # bounded/truncated review projection is W3.3.
            _fail(rs, er, "changeset_bounds", exit_code=exit_code, timed_out=timed_out)
            await db.flush()
            note = _observe_failure(
                rs, "changeset_bounds", er=er, had_command=bool(request.command)
            )
            return SandboxOutcome(
                runtime_session=rs, exec_run=er, stdout=stdout, stderr=stderr, failure_note=note
            )

        # Stage changed blobs (content-addressed dedup) + build the overlay deltas.
        #
        # PEAK-MEMORY DISCIPLINE (config §1.7). By this point the delta already holds the
        # only buffers that still matter, and `ws.files` / `result_files` hold the *same*
        # objects — the delta references them, it does not copy them. Dropping the two trees
        # here means the loop below retains one file's bytes at a time instead of a full
        # old tree plus a full new tree for the whole staging pass.
        #
        # Safety: `delta.entries` carries every byte that must be persisted, and the failure
        # paths below never re-read these trees — a failing exit still persists whatever the
        # delta captured, so the "edits are never lost" guarantee is untouched.
        ws.files.clear()
        ws.dirs.clear()
        if result_files is not ws.files:
            result_files.clear()
        del result_files

        overlay: list[wc_svc.OverlayDelta] = []
        # Destructive iteration: each entry's buffer is dropped as soon as its bytes are
        # durable, so staging holds **one** file at a time rather than the whole change set.
        entries = delta.entries
        for idx in range(len(entries)):
            d = entries[idx]
            if d.change_kind in ("added", "modified"):
                # `d.data` may be the bytearray egress filled. It is handed to the blob
                # store as a **buffer** — no `bytes()` copy — and the store makes the single
                # immutable snapshot it needs at its own boundary.
                h, _ = await drive_svc.ensure_blob(
                    db,
                    ctx,
                    wc.user_id,
                    data=d.data if d.data is not None else b"",
                    content_type="application/octet-stream",
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
            # Release this file's bytes before touching the next one.
            entries[idx] = _DROPPED
            del d
        entries.clear()

        published = True
        if overlay:
            published = await wc_svc.persist_overlay(
                db, ctx, wc, fence_token=fence, deltas=overlay, run_id=run_id
            )

        final_reason = reason if published else "fence_lost"
        if not published:
            _fail(rs, er, final_reason, exit_code=exit_code, timed_out=timed_out)
        else:
            _settle(rs, er, final_reason, exit_code=exit_code, timed_out=timed_out)
        await db.flush()

        # One log line + one redacted observation per FAILING exit; a clean `done` stays
        # silent. Note that a runtime failure still persists the host-side edits above —
        # the error is an observation for the model, never a crash (docs/04 invariant).
        settled_note: str | None = None
        if final_reason != "done":
            settled_note = _observe_failure(
                rs,
                final_reason,
                er=er,
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
            if er is not None:
                er.change_set_id = change_set_id
        return SandboxOutcome(
            runtime_session=rs,
            exec_run=er,
            stdout=stdout,
            stderr=stderr,
            change_set_id=change_set_id,
            failure_note=settled_note,
        )
    finally:
        # The disposable copy is a rebuildable cache — it is simply dropped; the durable
        # boundary is the persisted overlay, never the container. The session is closed on
        # EVERY exit so `uq_prs_live` never blocks the next boundary on this working copy.
        rs.container_ref = None
        if rs.state != "failed":
            rs.state = "closed"
        rs.closed_at = _now()


def sweep_orphan_scratch(*, protected_ids: frozenset[str] = frozenset()) -> int:
    """Startup sweep of sandbox containers left by crashed runs (rebuildable cache).

    Under tar transport there is no host scratch directory left to sweep (ADR-047 deleted
    ``SANDBOX_SCRATCH_ROOT``); what can leak is a container whose worker died mid-run."""
    return sbx.sweep_orphan_containers(protected_ids=protected_ids)
