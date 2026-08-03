"""Resume a run after an approval decision (api.md §6.3/§6.4; ADR-020).

When the user resolves a gated external action, the run must complete: an
``allow_*`` decision executes the bound invocation and settles it; a ``reject``
fails it with a bounded tool error. The args are recovered from the bound
``tool-call`` event and re-hashed against the envelope, so we execute exactly the
approved arguments. Idempotent on the invocation's settled state, so an at-least-once
redelivery of the resume wake-up is a safe no-op.

The caller owns the transaction (the worker job commits); expected failures
(unrecoverable args, tool error) are handled in-session as a settled-failed
outcome rather than raising, so no partial state is left behind.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import ACTION, record_receipt
from app.effects import (
    args_hash,
    claim_prepared,
    settle_failed,
    settle_stale_running_unknown,
    settle_succeeded,
)
from app.events import append_event, lock_event_sequences
from app.models import ApprovalEnvelope, EffectInvocation, EventJournal, Run
from app.observability import bind_context
from app.services.grants import grant_from_action
from app.tools import ToolContext, ToolError, bound_text, build_default_registry

_COMMITTED_DISPATCH_TOOLS = frozenset({"runtime_open", "runtime_close", "sh_exec"})
_RUNNING_STALE_SECONDS = 1300


def _tool_call_id(invocation: EffectInvocation) -> str:
    if invocation.turn_seq is None:
        raise ValueError("tool-call turn not recoverable for invocation")
    key_prefix = f"tool:{invocation.run_id}:{invocation.turn_seq}:"
    if not invocation.idempotency_key.startswith(key_prefix):
        raise ValueError("tool-call id not recoverable for invocation")
    call_id = invocation.idempotency_key[len(key_prefix) :]
    if not call_id:
        raise ValueError("tool-call id not recoverable for invocation")
    return call_id


async def _recover_tool_args(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invocation: EffectInvocation,
    tool_name: str,
    args_hash_target: bytes,
) -> tuple[dict[str, object], str]:
    """Recover a gated tool call's args + id from its bound ``tool-call`` event.

    The envelope stores only the args hash, so we match the run's ``tool-call``
    event whose name matches and whose args hash to the same value — an integrity
    check that the args we execute are exactly the approved ones.
    """
    expected_call_id = _tool_call_id(invocation)

    rows = (
        (
            await session.execute(
                select(EventJournal).where(
                    EventJournal.tenant_id == tenant_id,
                    EventJournal.run_id == invocation.run_id,
                    EventJournal.event_type == "tool-call",
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        payload = row.payload_redacted or {}
        if payload.get("id") != expected_call_id or payload.get("name") != tool_name:
            continue
        args = payload.get("args", {})
        if isinstance(args, dict) and args_hash(args) == args_hash_target:
            return args, expected_call_id
    raise ValueError("tool-call args not recoverable for envelope")


async def _fail(
    session: AsyncSession,
    env: ApprovalEnvelope,
    *,
    call_id: str,
    error: str,
    outcome: str,
    receipt: bool,
) -> str:
    await settle_failed(session, env.tenant_id, env.invocation_id, error=error[:500])
    await append_event(
        session,
        tenant_id=env.tenant_id,
        run_id=env.run_id,
        session_id=env.session_id,
        event_type="tool-error",
        payload={
            "id": call_id,
            "name": env.tool_name,
            "ok": False,
            "output": f"error: {error[:2000]}",
        },
    )
    if receipt:
        await record_receipt(
            session,
            tenant_id=env.tenant_id,
            receipt_type=ACTION,
            actor_type="user",
            actor_user_id=env.decided_by_user_id,
            trigger_type="approval",
            action=env.tool_name,
            outcome=outcome,
            run_id=env.run_id,
            invocation_id=env.invocation_id,
            approval_envelope_id=env.id,
            subject_type="approval_envelope",
            subject_id=env.id,
            summary={"decision": env.decision or ""},
        )
    return outcome


async def _queue_continuation_if_ready(session: AsyncSession, env: ApprovalEnvelope) -> None:
    """Queue the suspended run once every approval-bound invocation is terminal."""
    run = (
        await session.execute(
            select(Run)
            .where(Run.tenant_id == env.tenant_id, Run.id == env.run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        return
    unsettled = await session.scalar(
        select(EffectInvocation.invocation_id)
        .join(
            ApprovalEnvelope,
            (ApprovalEnvelope.tenant_id == EffectInvocation.tenant_id)
            & (ApprovalEnvelope.invocation_id == EffectInvocation.invocation_id),
        )
        .where(
            ApprovalEnvelope.tenant_id == env.tenant_id,
            ApprovalEnvelope.run_id == env.run_id,
            EffectInvocation.status != "settled",
        )
        .limit(1)
    )
    if unsettled is not None:
        return

    if run.status == "queued":
        return
    if run.status != "running" or run.settled_at is not None or run.worker_id is not None:
        return
    run.status = "queued"
    run.settled_at = None
    run.heartbeat_at = None
    run.lease_expires_at = None
    run.worker_id = None
    run.error_redacted = None
    await session.flush()


async def recover_stale_approval_resumes(
    session: AsyncSession, *, now: datetime.datetime | None = None, limit: int = 100
) -> list[uuid.UUID]:
    """Stop stale dispatched approvals as effect_unknown; never blind-retry."""
    current = now or datetime.datetime.now(datetime.UTC)
    cutoff = current - datetime.timedelta(seconds=_RUNNING_STALE_SECONDS)
    rows = (
        await session.execute(
            select(ApprovalEnvelope, EffectInvocation, Run)
            .join(
                EffectInvocation,
                (EffectInvocation.tenant_id == ApprovalEnvelope.tenant_id)
                & (EffectInvocation.invocation_id == ApprovalEnvelope.invocation_id),
            )
            .join(
                Run,
                (Run.tenant_id == ApprovalEnvelope.tenant_id) & (Run.id == ApprovalEnvelope.run_id),
            )
            .where(
                ApprovalEnvelope.status == "decided",
                EffectInvocation.status == "running",
                EffectInvocation.updated_at < cutoff,
                Run.status == "running",
            )
            .order_by(EffectInvocation.updated_at)
            .limit(limit)
        )
    ).all()
    grouped: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[ApprovalEnvelope, EffectInvocation]]] = {}
    for env, invocation, _run in rows:
        grouped.setdefault((env.tenant_id, env.run_id), []).append((env, invocation))

    terminal_run_ids: list[uuid.UUID] = []
    for (tenant_id, run_id), approvals in grouped.items():
        first_env = approvals[0][0]
        await lock_event_sequences(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=first_env.session_id,
        )
        locked_run = (
            await session.execute(
                select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_run is None or locked_run.status != "running":
            continue
        error = "effect_unknown: approval execution lost after dispatch claim"
        settled_any = False
        for env, invocation in approvals:
            if not await settle_stale_running_unknown(
                session,
                env.tenant_id,
                env.invocation_id,
                updated_before=cutoff,
                error=error,
            ):
                continue
            try:
                call_id = _tool_call_id(invocation)
            except ValueError:
                call_id = ""
            await append_event(
                session,
                tenant_id=env.tenant_id,
                run_id=env.run_id,
                session_id=env.session_id,
                event_type="tool-error",
                payload={
                    "id": call_id,
                    "name": env.tool_name,
                    "ok": False,
                    "output": f"error: {error}; reconciliation required",
                },
            )
            settled_any = True
        if not settled_any:
            continue
        locked_run.status = "needs_reconciliation"
        locked_run.settled_at = current
        locked_run.lease_expires_at = None
        locked_run.worker_id = None
        locked_run.error_redacted = error
        await append_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=first_env.session_id,
            event_type="run.settled",
            payload={"reason": "effect_unknown", "status": "needs_reconciliation"},
        )
        terminal_run_ids.append(run_id)
    await session.flush()
    return terminal_run_ids


async def resume_approval(session: AsyncSession, correlation_id: uuid.UUID) -> str:
    """Execute (allow) or fail (reject) a decided approval's bound invocation."""
    env = (
        await session.execute(
            select(ApprovalEnvelope).where(ApprovalEnvelope.correlation_id == correlation_id)
        )
    ).scalar_one_or_none()
    if env is None:
        return "unknown_envelope"
    if env.status != "decided":
        return f"not_decided:{env.status}"
    bind_context(
        tenant_id=str(env.tenant_id),
        run_id=str(env.run_id),
        session_id=str(env.session_id),
    )

    inv = await session.get(EffectInvocation, (env.tenant_id, env.invocation_id))
    if inv is None:
        return "unknown_invocation"
    if inv.status == "settled":
        # A committed-dispatch tool settles its effect/result before audit and
        # continuation bookkeeping. A retry after a crash must still re-arm the
        # suspended run instead of treating the partial resume as fully complete.
        await _queue_continuation_if_ready(session, env)
        return "already_settled"
    if inv.status == "running":
        return "already_running"
    if inv.status == "needs_reconciliation":
        return "effect_unknown"

    try:
        args, call_id = await _recover_tool_args(
            session, env.tenant_id, inv, env.tool_name, env.args_hash
        )
    except ValueError as exc:
        outcome = await _fail(
            session, env, call_id="", error=str(exc), outcome="failed", receipt=True
        )
        await _queue_continuation_if_ready(session, env)
        return outcome

    await lock_event_sequences(
        session,
        tenant_id=env.tenant_id,
        run_id=env.run_id,
        session_id=env.session_id,
    )
    run = (
        await session.execute(
            select(Run)
            .where(Run.tenant_id == env.tenant_id, Run.id == env.run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        run is None
        or run.status != "running"
        or run.settled_at is not None
        or run.worker_id is not None
    ):
        return f"run_not_suspended:{run.status if run is not None else 'missing'}"

    if not await claim_prepared(session, env.tenant_id, env.invocation_id):
        await session.refresh(inv)
        if inv.status == "settled":
            await _queue_continuation_if_ready(session, env)
            return "already_settled"
        if inv.status == "running":
            return "already_running"
        if inv.status == "needs_reconciliation":
            return "effect_unknown"
        return f"not_claimable:{inv.status}"

    if env.decision == "reject":
        outcome = await _fail(
            session,
            env,
            call_id=call_id,
            error="approval_rejected",
            outcome="rejected",
            receipt=True,
        )
        await _queue_continuation_if_ready(session, env)
        return outcome

    # allow_once / allow_session / always: execute the approved invocation.
    tool = build_default_registry().get(env.tool_name)
    # The atomic prepared -> running claim is durable before any approved side
    # effect. A redelivery observes running/settled and never executes it twice.
    await session.commit()
    tool_ctx = ToolContext(
        tenant_id=env.tenant_id,
        user_id=env.decided_by_user_id,
        session_id=env.session_id,
        run_id=env.run_id,
        invocation_id=env.invocation_id,
        session=session,
    )
    try:
        result = await tool.execute(tool_ctx, args)
    except ToolError as exc:
        outcome = await _fail(
            session, env, call_id=call_id, error=str(exc), outcome="failed", receipt=True
        )
        await _queue_continuation_if_ready(session, env)
        return outcome

    output = bound_text(result.llm_content).text
    await settle_succeeded(session, env.tenant_id, env.invocation_id, result={"approved": True})
    await append_event(
        session,
        tenant_id=env.tenant_id,
        run_id=env.run_id,
        session_id=env.session_id,
        event_type="tool-result",
        payload={"id": call_id, "name": env.tool_name, "ok": True, "output": output[:4000]},
    )
    if tool.name in _COMMITTED_DISPATCH_TOOLS:
        # The runtime service already committed its Docker/overlay boundary. Settle the
        # effect + tool result independently before audit/grant follow-up can fail.
        await session.commit()
    await record_receipt(
        session,
        tenant_id=env.tenant_id,
        receipt_type=ACTION,
        actor_type="user",
        actor_user_id=env.decided_by_user_id,
        trigger_type="approval",
        action=env.tool_name,
        outcome="succeeded",
        run_id=env.run_id,
        invocation_id=env.invocation_id,
        approval_envelope_id=env.id,
        subject_type="approval_envelope",
        subject_id=env.id,
        summary={"decision": env.decision or ""},
        reversible=False,
    )
    # `always` → persist a pre-authorization grant so matching actions auto-allow next
    # time (ADR-034). Owner-only; grantable tools only; a no-op otherwise.
    if env.decision == "always" and env.decided_by_user_id is not None:
        await grant_from_action(
            session,
            tenant_id=env.tenant_id,
            user_id=env.decided_by_user_id,
            tool_name=env.tool_name,
            args=args,
        )
    await _queue_continuation_if_ready(session, env)
    return "resumed"
