"""Explicit Project RuntimeSession lifecycle (ADR-048, Phase TR P4).

The database rows and working-copy overlay are durable authority. A Docker container is a
hot, rebuildable cache: open materializes the effective tree, every exec persists a bounded
egress delta before reporting completion, host-side fs edits may invalidate the cache, and
close/recovery removes it.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.events import publish_transient_session_event
from app.models import (
    ProjectExecRun,
    ProjectRuntimeSession,
    ProjectWorkingCopy,
    StorageBlob,
)
from app.models import Session as SessionModel
from app.objectstore import build_object_store
from app.sandbox import runtime as sbx
from app.services import ServiceError
from app.services import drive as drive_svc
from app.services import project_changes as changes_svc
from app.services import project_workcopy as wc_svc
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound
from app.services.project_sandbox import failure_note

logger = logging.getLogger("app.services.project_runtime")

_LIVE_RUNTIME_STATES = ("opening", "ready", "executing", "closing")
_COMMAND_PREVIEW_MAX = 2000
_OUTPUT_FIELD_MAX = 50_000
_OPERATION_GRACE_SECONDS = 300
_OPEN_CLOSE_JOB_TIMEOUT_SECONDS = 900


@dataclasses.dataclass(frozen=True)
class RuntimeAction:
    runtime_session: ProjectRuntimeSession
    failure_note: str | None = None


@dataclasses.dataclass(frozen=True)
class ExecAction:
    runtime_session: ProjectRuntimeSession
    exec_run: ProjectExecRun
    stdout: str
    stderr: str
    failure_note: str | None = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _user_id(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("runtime requires a user")
    return ctx.user_id


async def _owned_session(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> SessionModel:
    session = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if session is None or session.user_id != _user_id(ctx) or session.status == "deleted":
        raise NotFound("session not found")
    return session


async def _owned_runtime(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    runtime_session_id: uuid.UUID,
    session_id: uuid.UUID,
    lock: bool = False,
) -> ProjectRuntimeSession:
    stmt = select(ProjectRuntimeSession).where(
        ProjectRuntimeSession.tenant_id == ctx.tenant_id,
        ProjectRuntimeSession.id == runtime_session_id,
        ProjectRuntimeSession.user_id == _user_id(ctx),
        ProjectRuntimeSession.session_id == session_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    runtime = await db.scalar(stmt)
    if runtime is None:
        raise NotFound("runtime session not found")
    return runtime


def _materialize_entries(
    effective: dict[str, wc_svc.EffectiveEntry],
) -> list[sbx.MaterializeEntry]:
    return [
        sbx.MaterializeEntry(
            path=entry.path,
            entry_kind=entry.entry_kind,
            content_hash=entry.content_hash,
            size_bytes=entry.size_bytes,
            executable=entry.executable,
            symlink_target=entry.symlink_target,
        )
        for entry in effective.values()
    ]


async def _workspace(
    db: AsyncSession,
    ctx: CallerContext,
    runtime: ProjectRuntimeSession,
) -> sbx.Workspace:
    if runtime.scope == "ephemeral":
        return sbx.Workspace()
    if runtime.working_copy_id is None:
        raise Invalid("project runtime has no working copy")
    wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, runtime.working_copy_id))
    if wc is None or wc.user_id != _user_id(ctx):
        raise NotFound("working copy not found")
    effective = await wc_svc.effective_tree(db, ctx, wc)

    async def read_blob(content_hash: bytes) -> bytes:
        blob = await db.get(StorageBlob, (ctx.tenant_id, wc.user_id, content_hash))
        if blob is None:
            raise sbx.ScratchError("blob_missing")
        return await build_object_store().get(blob.object_key)

    return await sbx.materialize(_materialize_entries(effective), read_blob)


def _operation_expiry(timeout_seconds: int) -> datetime.datetime:
    return _now() + datetime.timedelta(seconds=timeout_seconds + _OPERATION_GRACE_SECONDS)


def _idle_expiry() -> datetime.datetime:
    return _now() + datetime.timedelta(seconds=settings.sandbox_runtime_idle_ttl_seconds)


async def _publish_state(
    runtime: ProjectRuntimeSession,
    *,
    exec_run: ProjectExecRun | None = None,
) -> None:
    try:
        await publish_transient_session_event(
            tenant_id=runtime.tenant_id,
            session_id=runtime.session_id,
            run_id=exec_run.run_id if exec_run is not None else None,
            event_type="runtime.state",
            payload={
                "runtime_session_id": str(runtime.id),
                "exec_run_id": str(exec_run.id) if exec_run is not None else None,
                "state": runtime.state,
                "termination_reason": (
                    exec_run.termination_reason
                    if exec_run is not None
                    else runtime.termination_reason
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001 - debug acceleration, DB remains truth
        logger.warning("runtime state publish failed", extra={"error_type": type(exc).__name__})


async def _activate_container(
    db: AsyncSession,
    ctx: CallerContext,
    runtime: ProjectRuntimeSession,
    workspace: sbx.Workspace,
    *,
    operation_id: uuid.UUID,
) -> RuntimeAction:
    outcome = await sbx.open_runtime_workspace(workspace, session_label=str(runtime.id))
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime.id,
        session_id=runtime.session_id,
        lock=True,
    )
    if runtime.operation_id != operation_id or runtime.operation_kind != "open":
        if outcome.container_ref is not None:
            await sbx.remove_runtime_container(outcome.container_ref)
        raise Conflict("runtime_operation_lost")
    if outcome.result.error is not None or outcome.container_ref is None:
        runtime.state = "failed"
        runtime.termination_reason = outcome.result.error or sbx.RUNTIME_START_FAILED
        runtime.container_ref = None
        runtime.operation_id = None
        runtime.operation_kind = None
        runtime.expires_at = _now()
        await db.commit()
        note = failure_note(runtime.termination_reason)
        logger.warning(
            "runtime open failed",
            extra={
                "runtime_session_id": str(runtime.id),
                "termination_reason": runtime.termination_reason,
                "sandbox_error_detail": outcome.result.error_detail,
            },
        )
        await _publish_state(runtime)
        return RuntimeAction(runtime, note)
    runtime.container_ref = outcome.container_ref
    runtime.operation_id = None
    runtime.operation_kind = None
    runtime.image_digest = outcome.image_digest
    runtime.capabilities = outcome.capabilities
    runtime.ingress_bytes = outcome.result.ingress_bytes
    runtime.entry_count = len(workspace.base_manifest)
    runtime.state = "ready"
    runtime.termination_reason = None
    runtime.expires_at = _idle_expiry()
    await db.commit()
    await _publish_state(runtime)
    return RuntimeAction(runtime)


async def open_runtime(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    scope: str = "project",
    reason: str | None = None,
) -> RuntimeAction:
    action, needs_activation = await prepare_runtime(
        db,
        ctx,
        session_id=session_id,
        scope=scope,
        reason=reason,
    )
    if not needs_activation:
        return action
    return await activate_runtime(
        db,
        ctx,
        session_id=session_id,
        runtime_session_id=action.runtime_session.id,
    )


async def prepare_runtime(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    scope: str = "project",
    reason: str | None = None,
) -> tuple[RuntimeAction, bool]:
    """Persist an opening RuntimeSession without touching Docker.

    Returns ``(action, needs_activation)`` so REST can enqueue the worker only when
    this call created or re-opened the container cache.
    """
    del reason  # bounded audit prose is carried by the tool/event, not a schema column
    if scope not in ("project", "ephemeral"):
        raise Invalid("bad runtime scope")
    session = await _owned_session(db, ctx, session_id=session_id)
    wc: ProjectWorkingCopy | None = None
    if scope == "project":
        if session.project_id is None:
            raise Invalid("project runtime requires a Project-bound chat")
        wc = await wc_svc.open_working_copy(db, ctx, session_id=session_id)
        locked_wc = await db.scalar(
            select(ProjectWorkingCopy)
            .where(
                ProjectWorkingCopy.tenant_id == ctx.tenant_id,
                ProjectWorkingCopy.id == wc.id,
            )
            .with_for_update()
        )
        if locked_wc is None:
            raise NotFound("working copy not found")
        wc = locked_wc
        existing = await db.scalar(
            select(ProjectRuntimeSession)
            .where(
                ProjectRuntimeSession.tenant_id == ctx.tenant_id,
                ProjectRuntimeSession.working_copy_id == wc.id,
                ProjectRuntimeSession.state.in_(_LIVE_RUNTIME_STATES),
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.state == "ready":
                if existing.container_ref is None:
                    existing.state = "opening"
                    existing.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
                    await db.commit()
                    await _publish_state(existing)
                    return RuntimeAction(existing), True
                existing.expires_at = _idle_expiry()
            if existing.state == "ready" and existing.working_copy_id is not None:
                existing_wc = await db.get(
                    ProjectWorkingCopy, (ctx.tenant_id, existing.working_copy_id)
                )
                if existing_wc is not None:
                    existing_wc.lease_expires_at = existing.expires_at
            await db.commit()
            return RuntimeAction(existing), False
        fence = await wc_svc.acquire_lease(
            db,
            wc,
            owner="runtime:opening",
            ttl_seconds=settings.sandbox_runtime_idle_ttl_seconds,
        )
    else:
        fence = None

    runtime = ProjectRuntimeSession(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        project_id=wc.project_id if wc is not None else None,
        working_copy_id=wc.id if wc is not None else None,
        session_id=session_id,
        user_id=_user_id(ctx),
        scope=scope,
        base_snapshot_id=wc.base_snapshot_id if wc is not None else None,
        fence_token=fence,
        state="opening",
        image=settings.sandbox_image or "",
        expires_at=_operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS),
    )
    if wc is not None:
        wc.lease_owner = f"runtime:{runtime.id}"
        wc.lease_expires_at = runtime.expires_at
    db.add(runtime)
    await db.commit()  # durable row before Docker create/ingress/start
    await _publish_state(runtime)
    return RuntimeAction(runtime), True


async def activate_runtime(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
) -> RuntimeAction:
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime_session_id,
        session_id=session_id,
        lock=True,
    )
    if runtime.state == "ready" and runtime.container_ref is not None:
        return RuntimeAction(runtime)
    if runtime.state != "opening":
        raise Conflict("runtime_not_opening")
    if runtime.operation_id is not None:
        raise Conflict("runtime_operation_in_progress")
    operation_id = uuid.uuid4()
    runtime.operation_id = operation_id
    runtime.operation_kind = "open"
    runtime.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
    await db.commit()
    try:
        workspace = await _workspace(db, ctx, runtime)
    except sbx.ScratchError as exc:
        runtime = await _owned_runtime(
            db,
            ctx,
            runtime_session_id=runtime.id,
            session_id=session_id,
            lock=True,
        )
        runtime.state = "failed"
        runtime.termination_reason = exc.code
        runtime.operation_id = None
        runtime.operation_kind = None
        runtime.expires_at = _now()
        await db.commit()
        return RuntimeAction(runtime, failure_note(exc.code))
    return await _activate_container(db, ctx, runtime, workspace, operation_id=operation_id)


async def _ensure_ready(
    db: AsyncSession,
    ctx: CallerContext,
    runtime: ProjectRuntimeSession,
) -> tuple[ProjectRuntimeSession, RuntimeAction | None]:
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime.id,
        session_id=runtime.session_id,
        lock=True,
    )
    if runtime.state == "ready" and runtime.container_ref:
        return runtime, None
    if runtime.state == "executing":
        raise Conflict("runtime_busy")
    if runtime.state == "opening":
        raise Conflict("runtime_opening")
    if runtime.state in ("closed", "failed"):
        raise Conflict("runtime_not_live")
    if runtime.state != "ready" or runtime.container_ref is not None:
        raise Conflict("runtime_not_ready")
    runtime.state = "opening"
    runtime.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
    await db.commit()
    await _publish_state(runtime)
    action = await activate_runtime(
        db,
        ctx,
        session_id=runtime.session_id,
        runtime_session_id=runtime.id,
    )
    return action.runtime_session, action


async def _stage_delta(
    db: AsyncSession,
    ctx: CallerContext,
    runtime: ProjectRuntimeSession,
    baseline: sbx.Workspace,
    result_files: dict[str, sbx.WorkspaceFile],
    *,
    run_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, str | None]:
    if runtime.scope == "ephemeral":
        return None, None
    if runtime.working_copy_id is None or runtime.fence_token is None:
        raise Invalid("project runtime is missing its working-copy fence")
    wc = await db.scalar(
        select(ProjectWorkingCopy)
        .where(
            ProjectWorkingCopy.tenant_id == ctx.tenant_id,
            ProjectWorkingCopy.id == runtime.working_copy_id,
        )
        .with_for_update()
    )
    if wc is None:
        raise NotFound("working copy not found")
    delta = sbx.compute_delta(baseline, result_files)
    if delta.over_bounds:
        return None, "changeset_bounds"

    overlay: list[wc_svc.OverlayDelta] = []
    for entry in delta.entries:
        if entry.change_kind in ("added", "modified"):
            content_hash, _ = await drive_svc.ensure_blob(
                db,
                ctx,
                wc.user_id,
                data=entry.data if entry.data is not None else b"",
                content_type="application/octet-stream",
            )
            overlay.append(
                wc_svc.OverlayDelta(
                    path=entry.path,
                    change_kind=entry.change_kind,
                    content_hash=content_hash,
                    size_bytes=entry.size_bytes,
                    executable=entry.executable,
                )
            )
        else:
            overlay.append(wc_svc.OverlayDelta(path=entry.path, change_kind="deleted"))
    if overlay:
        published = await wc_svc.persist_overlay(
            db,
            ctx,
            wc,
            fence_token=runtime.fence_token,
            deltas=overlay,
            run_id=run_id,
        )
        if not published:
            return None, "fence_lost"
        change_set = await changes_svc.build_change_set(db, ctx, wc, run_id=run_id)
    else:
        change_set = await changes_svc.open_change_set(db, ctx, wc)
    return change_set.id if change_set is not None else None, None


def _validated_exec_request(command: str, timeout_seconds: int | None) -> tuple[str, int]:
    cleaned = command.strip()
    if not cleaned or len(cleaned) > 4000:
        raise Invalid("command must be 1..4000 characters")
    timeout = timeout_seconds or settings.sandbox_run_timeout_seconds
    if not 1 <= timeout <= 900:
        raise Invalid("timeout_seconds must be between 1 and 900")
    return cleaned, timeout


async def _next_exec_seq(
    db: AsyncSession, ctx: CallerContext, runtime_session_id: uuid.UUID
) -> int:
    last_seq = await db.scalar(
        select(func.coalesce(func.max(ProjectExecRun.seq), 0)).where(
            ProjectExecRun.tenant_id == ctx.tenant_id,
            ProjectExecRun.runtime_session_id == runtime_session_id,
        )
    )
    return int(last_seq or 0) + 1


async def prepare_exec(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
    command: str,
    timeout_seconds: int | None = None,
) -> ProjectExecRun:
    """Persist one human REST exec and mark the runtime busy without touching Docker."""
    command, timeout = _validated_exec_request(command, timeout_seconds)
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime_session_id,
        session_id=session_id,
        lock=True,
    )
    if runtime.state != "ready" or runtime.container_ref is None:
        raise Conflict("runtime_not_ready")
    exec_run = ProjectExecRun(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        runtime_session_id=runtime.id,
        run_id=None,
        invocation_id=None,
        seq=await _next_exec_seq(db, ctx, runtime.id),
        command_text=command,
        command_preview=command[:_COMMAND_PREVIEW_MAX],
        timeout_seconds=timeout,
        state="queued",
    )
    db.add(exec_run)
    runtime.state = "executing"
    runtime.expires_at = _operation_expiry(timeout)
    if runtime.working_copy_id is not None:
        wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, runtime.working_copy_id))
        if wc is not None:
            wc.lease_expires_at = runtime.expires_at
    await db.commit()
    await _publish_state(runtime, exec_run=exec_run)
    return exec_run


async def exec_runtime(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
    command: str,
    timeout_seconds: int | None = None,
    run_id: uuid.UUID | None = None,
    invocation_id: uuid.UUID | None = None,
    prepared_exec_id: uuid.UUID | None = None,
) -> ExecAction:
    if prepared_exec_id is not None:
        runtime = await _owned_runtime(
            db,
            ctx,
            runtime_session_id=runtime_session_id,
            session_id=session_id,
            lock=True,
        )
        exec_run = await db.scalar(
            select(ProjectExecRun)
            .where(
                ProjectExecRun.tenant_id == ctx.tenant_id,
                ProjectExecRun.id == prepared_exec_id,
                ProjectExecRun.runtime_session_id == runtime_session_id,
            )
            .with_for_update()
        )
        if exec_run is None:
            raise NotFound("exec run not found")
        if exec_run.state in ("persisted", "failed", "cancelled"):
            return ExecAction(
                runtime,
                exec_run,
                exec_run.stdout_head or "",
                exec_run.stderr_tail or "",
                (
                    failure_note(exec_run.termination_reason)
                    if exec_run.termination_reason not in (None, "done")
                    else None
                ),
            )
        if exec_run.state != "queued" or runtime.state != "executing":
            raise Conflict("exec_in_progress")
        if runtime.container_ref is None:
            raise Conflict("runtime_not_ready")
        command = exec_run.command_text
        timeout = exec_run.timeout_seconds
        run_id = exec_run.run_id
        baseline = await _workspace(db, ctx, runtime)
        exec_run.state = "running"
        await db.commit()
        await _publish_state(runtime, exec_run=exec_run)
    else:
        command, timeout = _validated_exec_request(command, timeout_seconds)
        runtime = await _owned_runtime(
            db,
            ctx,
            runtime_session_id=runtime_session_id,
            session_id=session_id,
            lock=True,
        )
        if invocation_id is not None:
            existing = await db.scalar(
                select(ProjectExecRun).where(
                    ProjectExecRun.tenant_id == ctx.tenant_id,
                    ProjectExecRun.invocation_id == invocation_id,
                )
            )
            if existing is not None:
                if existing.state in ("persisted", "failed", "cancelled"):
                    return ExecAction(
                        runtime,
                        existing,
                        existing.stdout_head or "",
                        existing.stderr_tail or "",
                        (
                            failure_note(existing.termination_reason)
                            if existing.termination_reason not in (None, "done")
                            else None
                        ),
                    )
                raise Conflict("exec_in_progress")

        runtime, open_action = await _ensure_ready(db, ctx, runtime)
        if open_action is not None and open_action.failure_note is not None:
            raise Conflict(open_action.runtime_session.termination_reason or "runtime_open_failed")
        if runtime.container_ref is None:
            raise Conflict("runtime_not_ready")
        try:
            baseline = await _workspace(db, ctx, runtime)
        except sbx.ScratchError as exc:
            raise Conflict(exc.code) from exc

        runtime = await _owned_runtime(
            db,
            ctx,
            runtime_session_id=runtime.id,
            session_id=session_id,
            lock=True,
        )
        if runtime.state != "ready" or runtime.container_ref is None:
            raise Conflict("runtime_not_ready")
        exec_run = ProjectExecRun(
            tenant_id=ctx.tenant_id,
            id=uuid.uuid4(),
            runtime_session_id=runtime.id,
            run_id=run_id,
            invocation_id=invocation_id,
            seq=await _next_exec_seq(db, ctx, runtime.id),
            command_text=command,
            command_preview=command[:_COMMAND_PREVIEW_MAX],
            timeout_seconds=timeout,
            state="running",
        )
        db.add(exec_run)
        runtime.state = "executing"
        runtime.expires_at = _operation_expiry(timeout)
        if runtime.working_copy_id is not None:
            wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, runtime.working_copy_id))
            if wc is not None:
                wc.lease_expires_at = runtime.expires_at
        await db.commit()
        await _publish_state(runtime, exec_run=exec_run)

    assert exec_run is not None
    assert runtime.container_ref is not None
    active_exec: ProjectExecRun = exec_run
    container_ref: str = runtime.container_ref
    output_seq = 0

    async def on_output(stream: str, delta: str) -> None:
        nonlocal output_seq
        output_seq += 1
        try:
            await publish_transient_session_event(
                tenant_id=runtime.tenant_id,
                session_id=runtime.session_id,
                run_id=run_id,
                event_type="runtime.output",
                payload={
                    "runtime_session_id": str(runtime.id),
                    "exec_run_id": str(active_exec.id),
                    "stream": stream,
                    "seq": output_seq,
                    "delta": delta,
                },
            )
        except Exception as exc:  # noqa: BLE001 - debug acceleration only
            logger.warning(
                "runtime output publish failed",
                extra={"exec_run_id": str(active_exec.id), "error_type": type(exc).__name__},
            )

    async def cancel_requested() -> bool:
        await db.refresh(active_exec, attribute_names=["cancel_requested_at"])
        return active_exec.cancel_requested_at is not None

    exec_started = time.monotonic()
    outcome = await sbx.exec_runtime_command(
        container_ref,
        command,
        timeout_seconds=timeout,
        on_output=on_output,
        cancel_requested=cancel_requested,
    )
    result = outcome.result
    reason = "done"
    if outcome.cancelled:
        reason = "cancelled"
    elif result.error is not None:
        reason = result.error
    elif result.timed_out:
        reason = "wall_timeout"
    elif result.exit_code == 127:
        reason = "environment_missing_dependencies"

    change_set_id: uuid.UUID | None = None
    boundary_error: str | None = None
    if outcome.files is not None:
        change_set_id, boundary_error = await _stage_delta(
            db, ctx, runtime, baseline, outcome.files, run_id=run_id
        )
        if boundary_error is not None:
            reason = boundary_error

    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime.id,
        session_id=session_id,
        lock=True,
    )
    persisted_exec = await db.get(ProjectExecRun, (ctx.tenant_id, exec_run.id))
    if persisted_exec is None:
        raise NotFound("exec run not found")
    exec_run = persisted_exec
    exec_run.exit_code = (
        None
        if result.error is not None or outcome.cancelled or result.timed_out
        else result.exit_code
    )
    exec_run.timed_out = result.timed_out
    exec_run.termination_reason = reason
    exec_run.stdout_head = result.stdout[:_OUTPUT_FIELD_MAX]
    exec_run.stderr_tail = result.stderr[-_OUTPUT_FIELD_MAX:]
    exec_run.output_truncated = result.output_truncated
    exec_run.change_set_id = change_set_id
    exec_run.duration_ms = int((time.monotonic() - exec_started) * 1000)
    if outcome.files is not None and boundary_error is None:
        exec_run.persisted_boundary_at = _now()
    if reason == "cancelled":
        exec_run.state = "cancelled"
    elif reason == "done":
        exec_run.state = "persisted"
    else:
        exec_run.state = "failed"

    if (
        runtime.scope == "project"
        and runtime.working_copy_id is not None
        and (result.stdout or result.stderr)
    ):
        wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, runtime.working_copy_id))
        if wc is not None:
            combined = (
                result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else "")
            ).encode("utf-8", "replace")
            await changes_svc.record_artifact(
                db,
                ctx,
                wc,
                run_id=run_id,
                name=f"run-{exec_run.seq}.log",
                data=combined,
            )

    keep_container = outcome.container_alive and reason in (
        "done",
        "environment_missing_dependencies",
    )
    removed_container_ref: str | None = None
    if keep_container:
        runtime.state = "ready"
        runtime.termination_reason = None
        runtime.expires_at = _idle_expiry()
    elif reason in ("cancelled", "wall_timeout", sbx.MEM_LIMIT):
        removed_container_ref = runtime.container_ref
        runtime.state = "ready"
        runtime.container_ref = None
        runtime.termination_reason = None
        runtime.expires_at = _idle_expiry()
    else:
        removed_container_ref = runtime.container_ref
        runtime.state = "failed"
        runtime.container_ref = None
        runtime.termination_reason = reason
        runtime.expires_at = _now()
    if runtime.working_copy_id is not None:
        wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, runtime.working_copy_id))
        if wc is not None:
            wc.lease_expires_at = runtime.expires_at
    await db.commit()
    if removed_container_ref is not None:
        await sbx.remove_runtime_container(removed_container_ref)
    await _publish_state(runtime, exec_run=exec_run)

    note = None if reason == "done" else failure_note(reason)
    if note is not None:
        logger.warning(
            "runtime exec failed",
            extra={
                "runtime_session_id": str(runtime.id),
                "exec_run_id": str(exec_run.id),
                "termination_reason": reason,
                "sandbox_error_detail": result.error_detail,
            },
        )
    return ExecAction(runtime, exec_run, result.stdout, result.stderr, note)


async def request_cancel(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
) -> ProjectRuntimeSession:
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime_session_id,
        session_id=session_id,
        lock=True,
    )
    if runtime.state != "executing":
        raise Conflict("runtime_not_executing")
    exec_run = await db.scalar(
        select(ProjectExecRun)
        .where(
            ProjectExecRun.tenant_id == ctx.tenant_id,
            ProjectExecRun.runtime_session_id == runtime.id,
            ProjectExecRun.state.in_(("queued", "running")),
        )
        .order_by(ProjectExecRun.seq.desc())
        .limit(1)
        .with_for_update()
    )
    if exec_run is None:
        raise Conflict("exec_not_running")
    exec_run.cancel_requested_at = exec_run.cancel_requested_at or _now()
    await db.commit()
    return runtime


async def list_exec_runs(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    limit: int = 50,
) -> list[ProjectExecRun]:
    await _owned_session(db, ctx, session_id=session_id)
    if not 1 <= limit <= 200:
        raise Invalid("limit must be between 1 and 200")
    rows = (
        (
            await db.execute(
                select(ProjectExecRun)
                .join(
                    ProjectRuntimeSession,
                    (ProjectRuntimeSession.tenant_id == ProjectExecRun.tenant_id)
                    & (ProjectRuntimeSession.id == ProjectExecRun.runtime_session_id),
                )
                .where(
                    ProjectExecRun.tenant_id == ctx.tenant_id,
                    ProjectRuntimeSession.session_id == session_id,
                    ProjectRuntimeSession.user_id == _user_id(ctx),
                )
                .order_by(ProjectExecRun.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def recover_pending_jobs(
    db: AsyncSession,
) -> tuple[
    list[tuple[uuid.UUID, uuid.UUID]],
    list[tuple[uuid.UUID, uuid.UUID]],
    list[tuple[uuid.UUID, uuid.UUID]],
]:
    """Re-arm committed REST jobs lost between Postgres commit and Redis enqueue."""
    now = _now()
    open_jobs: list[tuple[uuid.UUID, uuid.UUID]] = []
    exec_jobs: list[tuple[uuid.UUID, uuid.UUID]] = []
    close_jobs: list[tuple[uuid.UUID, uuid.UUID]] = []
    runtimes = (
        (
            await db.execute(
                select(ProjectRuntimeSession)
                .where(
                    ProjectRuntimeSession.expires_at <= now,
                    ProjectRuntimeSession.state.in_(("opening", "executing", "closing")),
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for runtime in runtimes:
        if (
            runtime.state == "opening"
            and runtime.container_ref is None
            and runtime.operation_id is None
        ):
            runtime.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
            open_jobs.append((runtime.tenant_id, runtime.id))
            continue
        if runtime.state == "closing" and runtime.operation_id is None:
            runtime.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
            close_jobs.append((runtime.tenant_id, runtime.id))
            continue
        if runtime.state == "executing":
            queued = await db.scalar(
                select(ProjectExecRun)
                .where(
                    ProjectExecRun.tenant_id == runtime.tenant_id,
                    ProjectExecRun.runtime_session_id == runtime.id,
                    ProjectExecRun.state == "queued",
                )
                .order_by(ProjectExecRun.seq.desc())
                .limit(1)
            )
            if queued is not None:
                runtime.expires_at = _operation_expiry(queued.timeout_seconds)
                exec_jobs.append((runtime.tenant_id, queued.id))
    await db.commit()
    return open_jobs, exec_jobs, close_jobs


async def close_runtime(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
) -> RuntimeAction:
    action, needs_close = await prepare_close(
        db,
        ctx,
        session_id=session_id,
        runtime_session_id=runtime_session_id,
    )
    if not needs_close:
        return action
    return await finish_close(
        db,
        ctx,
        session_id=session_id,
        runtime_session_id=runtime_session_id,
    )


async def prepare_close(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
) -> tuple[RuntimeAction, bool]:
    """Persist ``closing`` for a worker-owned teardown without touching Docker."""
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime_session_id,
        session_id=session_id,
        lock=True,
    )
    if runtime.state == "executing":
        raise Conflict("runtime_busy")
    if runtime.state == "closing":
        raise Conflict("runtime_closing")
    if runtime.state == "closed":
        return RuntimeAction(runtime), False
    runtime.state = "closing"
    runtime.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
    await db.commit()
    await _publish_state(runtime)
    return RuntimeAction(runtime), True


async def finish_close(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    runtime_session_id: uuid.UUID,
) -> RuntimeAction:
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime_session_id,
        session_id=session_id,
        lock=True,
    )
    if runtime.state == "closed":
        return RuntimeAction(runtime)
    if runtime.state != "closing":
        raise Conflict("runtime_not_closing")
    if runtime.operation_id is not None:
        raise Conflict("runtime_operation_in_progress")
    operation_id = uuid.uuid4()
    runtime.operation_id = operation_id
    runtime.operation_kind = "close"
    runtime.expires_at = _operation_expiry(_OPEN_CLOSE_JOB_TIMEOUT_SECONDS)
    container_ref = runtime.container_ref
    await db.commit()
    close_failure: str | None = None
    if container_ref and runtime.scope == "project":
        try:
            baseline = await _workspace(db, ctx, runtime)
            result_files = await sbx.snapshot_runtime_workspace(container_ref)
            _change_set_id, close_failure = await _stage_delta(
                db, ctx, runtime, baseline, result_files, run_id=ctx.run_id
            )
            await db.commit()
        except (ServiceError, sbx.ScratchError, OSError) as exc:
            close_failure = (
                exc.code
                if isinstance(exc, (ServiceError, sbx.ScratchError))
                else "runtime_transport_failed"
            )
    if container_ref:
        await sbx.remove_runtime_container(container_ref)
    runtime = await _owned_runtime(
        db,
        ctx,
        runtime_session_id=runtime.id,
        session_id=session_id,
        lock=True,
    )
    if runtime.operation_id != operation_id or runtime.operation_kind != "close":
        raise Conflict("runtime_operation_lost")
    runtime.container_ref = None
    runtime.operation_id = None
    runtime.operation_kind = None
    runtime.state = "failed" if close_failure is not None else "closed"
    runtime.termination_reason = close_failure
    runtime.closed_at = _now()
    runtime.expires_at = _now()
    if runtime.working_copy_id is not None:
        wc = await db.get(ProjectWorkingCopy, (ctx.tenant_id, runtime.working_copy_id))
        if wc is not None and wc.lease_owner == f"runtime:{runtime.id}":
            wc.lease_owner = None
            wc.lease_expires_at = None
    await db.commit()
    await _publish_state(runtime)
    return RuntimeAction(
        runtime, failure_note(close_failure) if close_failure is not None else None
    )


async def protected_container_refs(db: AsyncSession) -> frozenset[str]:
    now = _now()
    refs = (
        (
            await db.execute(
                select(ProjectRuntimeSession.container_ref).where(
                    ProjectRuntimeSession.state.in_(_LIVE_RUNTIME_STATES),
                    ProjectRuntimeSession.expires_at > now,
                    ProjectRuntimeSession.container_ref.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return frozenset(ref for ref in refs if ref)


async def recover_expired(db: AsyncSession) -> tuple[int, list[str]]:
    now = _now()
    rows = (
        (
            await db.execute(
                select(ProjectRuntimeSession)
                .where(
                    ProjectRuntimeSession.state.in_(_LIVE_RUNTIME_STATES),
                    ProjectRuntimeSession.expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    refs: list[str] = []
    failed = 0
    for runtime in rows:
        queued_exec = None
        if runtime.state == "executing":
            queued_exec = await db.scalar(
                select(ProjectExecRun)
                .where(
                    ProjectExecRun.tenant_id == runtime.tenant_id,
                    ProjectExecRun.runtime_session_id == runtime.id,
                    ProjectExecRun.state == "queued",
                )
                .order_by(ProjectExecRun.seq.desc())
                .limit(1)
            )
        # These are committed-but-not-dispatched REST jobs. The 30s recovery tick
        # re-arms them; they are not failed as crashed in-flight work.
        if (
            (
                runtime.state == "opening"
                and runtime.container_ref is None
                and runtime.operation_id is None
            )
            or (runtime.state == "closing" and runtime.operation_id is None)
            or queued_exec is not None
        ):
            continue
        if runtime.container_ref:
            refs.append(runtime.container_ref)
        runtime.container_ref = None
        runtime.operation_id = None
        runtime.operation_kind = None
        runtime.state = "failed"
        runtime.termination_reason = "error:RuntimeExpired"
        runtime.closed_at = now
        if runtime.working_copy_id is not None:
            wc = await db.get(ProjectWorkingCopy, (runtime.tenant_id, runtime.working_copy_id))
            if wc is not None and wc.lease_owner == f"runtime:{runtime.id}":
                wc.lease_owner = None
                wc.lease_expires_at = None
        running_exec = await db.scalar(
            select(ProjectExecRun)
            .where(
                ProjectExecRun.tenant_id == runtime.tenant_id,
                ProjectExecRun.runtime_session_id == runtime.id,
                ProjectExecRun.state.in_(("queued", "running")),
            )
            .order_by(ProjectExecRun.seq.desc())
            .limit(1)
        )
        if running_exec is not None:
            running_exec.state = "failed"
            running_exec.termination_reason = "error:RuntimeExpired"
        failed += 1
    await db.commit()
    return failed, refs
