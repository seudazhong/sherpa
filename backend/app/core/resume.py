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

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import ACTION, record_receipt
from app.effects import args_hash, mark_running, settle_failed, settle_succeeded
from app.events import append_event
from app.models import ApprovalEnvelope, EffectInvocation, EventJournal
from app.observability import bind_context
from app.tools import ToolContext, ToolError, bound_text, build_default_registry


async def _recover_tool_args(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_name: str,
    args_hash_target: bytes,
) -> tuple[dict[str, object], str]:
    """Recover a gated tool call's args + id from its bound ``tool-call`` event.

    The envelope stores only the args hash, so we match the run's ``tool-call``
    event whose name matches and whose args hash to the same value — an integrity
    check that the args we execute are exactly the approved ones.
    """
    rows = (
        (
            await session.execute(
                select(EventJournal).where(
                    EventJournal.tenant_id == tenant_id,
                    EventJournal.run_id == run_id,
                    EventJournal.event_type == "tool-call",
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        payload = row.payload_redacted or {}
        if payload.get("name") != tool_name:
            continue
        args = payload.get("args", {})
        if isinstance(args, dict) and args_hash(args) == args_hash_target:
            return args, str(payload.get("id", ""))
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
        return "already_settled"

    if env.decision == "reject":
        return await _fail(
            session, env, call_id="", error="approval_rejected", outcome="rejected", receipt=True
        )

    # allow_once / allow_session / always: execute the approved invocation.
    try:
        args, call_id = await _recover_tool_args(
            session, env.tenant_id, env.run_id, env.tool_name, env.args_hash
        )
    except ValueError as exc:
        return await _fail(session, env, call_id="", error=str(exc), outcome="failed", receipt=True)

    await mark_running(session, env.tenant_id, env.invocation_id)
    tool = build_default_registry().get(env.tool_name)
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
        return await _fail(
            session, env, call_id=call_id, error=str(exc), outcome="failed", receipt=True
        )

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
    return "resumed"
