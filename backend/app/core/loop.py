"""The agent loop (docs/04-core-loop.md).

Bounded loop over a run: assemble transcript → stream the provider → gate tool
execution on the structured stop reason → execute tools through the effect/idempotency
path → append events → persist the assistant turn → repeat until a named termination.
Turn-granular persistence (ADR-006): the caller commits per turn so a crash resumes
from the last committed turn; tools are idempotent via begin_invocation.

Transcript messages are user/assistant/system only; tool calls/results live in the
event journal (the audit/observability source), and tool outputs are fed back to the
provider as synthesized `user` messages for continuation.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections import defaultdict

from sqlalchemy import func, select

from app.audit import ACTION, record_receipt
from app.effects import begin_invocation, mark_running, settle_failed, settle_succeeded
from app.events import append_event
from app.models import Message, Part, Run, Session
from app.permissions import policy as perm_policy
from app.permissions import request_approval
from app.providers import Finish, Provider, TextDelta, ToolCall
from app.tools import FULL, ToolError, ToolRegistry, bound_text

SYSTEM_PROMPT = "You are Sherpa, a careful assistant. Use tools when needed; be concise."


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _next_seq(session, tenant_id: uuid.UUID, session_id: uuid.UUID) -> int:  # type: ignore[no-untyped-def]
    val = await session.scalar(
        select(func.coalesce(func.max(Message.seq), 0) + 1).where(
            Message.tenant_id == tenant_id, Message.session_id == session_id
        )
    )
    return int(val)


async def _persist_message(  # type: ignore[no-untyped-def]
    session,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    role: str,
    text: str,
    author_user_id: uuid.UUID | None = None,
) -> None:
    seq = await _next_seq(session, tenant_id, session_id)
    message_id = uuid.uuid4()
    session.add(
        Message(
            tenant_id=tenant_id,
            id=message_id,
            session_id=session_id,
            run_id=run_id,
            author_user_id=author_user_id,
            seq=seq,
            role=role,
        )
    )
    await session.flush()
    session.add(
        Part(
            tenant_id=tenant_id,
            id=uuid.uuid4(),
            message_id=message_id,
            ordinal=0,
            kind="text",
            content_redacted={"text": text},
        )
    )
    await session.flush()


async def _load_transcript(  # type: ignore[no-untyped-def]
    session, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> list[dict[str, object]]:
    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    if not messages:
        return []
    ids = [m.id for m in messages]
    parts = (
        (
            await session.execute(
                select(Part)
                .where(Part.tenant_id == tenant_id, Part.message_id.in_(ids))
                .order_by(Part.ordinal)
            )
        )
        .scalars()
        .all()
    )
    by_msg: dict[uuid.UUID, list[Part]] = defaultdict(list)
    for p in parts:
        by_msg[p.message_id].append(p)
    out: list[dict[str, object]] = []
    for m in messages:
        text = " ".join(str(p.content_redacted.get("text", "")) for p in by_msg.get(m.id, []))
        out.append({"role": m.role, "content": text})
    return out


async def _run_tool(  # type: ignore[no-untyped-def]
    session,
    run: Run,
    turn: int,
    call: ToolCall,
    registry: ToolRegistry,
    provider_messages: list[dict[str, object]],
    *,
    decider_user_id: uuid.UUID | None,
) -> None:
    try:
        tool = registry.get(call.name)
    except ToolError as exc:
        await append_event(
            session,
            tenant_id=run.tenant_id,
            run_id=run.id,
            session_id=run.session_id,
            event_type="tool-error",
            payload={"id": call.id, "name": call.name, "ok": False, "output": f"error: {exc}"},
        )
        provider_messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": f"error: {exc}"}
        )
        return

    effect_class = perm_policy.classify_effect(tool.flags)
    key = f"tool:{run.id}:{turn}:{call.id}"
    handle = await begin_invocation(
        session,
        tenant_id=run.tenant_id,
        run_id=run.id,
        effect_name=call.name,
        idempotency_key=key,
        effect_class=effect_class,
        retry_policy="transient_before_dispatch",
        args=call.args,
        turn_seq=turn,
    )

    # Permission gate (ADR-020): a non-read-only action is not dispatched without an
    # approval. Persist a pending envelope bound to this invocation, surface the ask,
    # and feed the model a bounded observation that the action was NOT performed.
    if perm_policy.requires_approval(tool) and decider_user_id is not None:
        created = await request_approval(
            session,
            tenant_id=run.tenant_id,
            run_id=run.id,
            session_id=run.session_id,  # type: ignore[arg-type]
            invocation_id=handle.invocation_id,
            tool_name=tool.name,
            effect_class=effect_class,
            args=call.args,
            decider_user_id=decider_user_id,
        )
        env = created.envelope
        await append_event(
            session,
            tenant_id=run.tenant_id,
            run_id=run.id,
            session_id=run.session_id,
            event_type="permission.asked",
            payload={
                "correlation_id": str(env.correlation_id),
                "tool_name": env.tool_name,
                "permission_scope": env.permission_scope,
                "effect_class": env.effect_class,
                "preview": env.preview_redacted,
                "expires_at": env.expires_at.isoformat(),
                "nonce": created.nonce,
            },
        )
        await record_receipt(
            session,
            tenant_id=run.tenant_id,
            receipt_type=ACTION,
            actor_type="system",
            trigger_type="agent",
            action=tool.name,
            outcome="awaiting_approval",
            run_id=run.id,
            invocation_id=handle.invocation_id,
            approval_envelope_id=env.id,
            subject_type="approval_envelope",
            subject_id=env.id,
            summary={"permission_scope": env.permission_scope},
            reversible=True,
        )
        observation = (
            f"permission_required: approval requested for {tool.name} "
            f"(correlation {env.correlation_id}); the action was NOT performed and awaits "
            "the user's decision."
        )
        provider_messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
        return

    await mark_running(session, run.tenant_id, handle.invocation_id)
    ok = True
    try:
        result = await tool.execute(call.args)
        bounded = bound_text(result.llm_content)
        output = bounded.text
        await settle_succeeded(
            session, run.tenant_id, handle.invocation_id, result={"truncated": bounded.truncated}
        )
    except ToolError as exc:
        ok = False
        output = f"error: {exc}"
        await settle_failed(session, run.tenant_id, handle.invocation_id, error=str(exc))

    await append_event(
        session,
        tenant_id=run.tenant_id,
        run_id=run.id,
        session_id=run.session_id,
        event_type="tool-result" if ok else "tool-error",
        payload={"id": call.id, "name": call.name, "ok": ok, "output": output[:4000]},
    )
    provider_messages.append({"role": "tool", "tool_call_id": call.id, "content": output})


async def execute_run(  # type: ignore[no-untyped-def]
    session,
    *,
    run: Run,
    provider: Provider,
    registry: ToolRegistry,
    tier: str = FULL,
    max_turns: int = 25,
) -> str:
    """Run the bounded loop to a named termination; returns the reason string."""
    if run.session_id is None:
        raise ValueError("execute_run requires a session-bound run")
    tenant_id, run_id, session_id = run.tenant_id, run.id, run.session_id
    decider_user_id = await session.scalar(
        select(Session.user_id).where(Session.tenant_id == tenant_id, Session.id == session_id)
    )

    run.status = "running"
    run.started_at = _now()
    await session.flush()
    await append_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        session_id=session_id,
        event_type="run.started",
        payload={"run_kind": run.run_kind},
    )

    transcript = await _load_transcript(session, tenant_id, session_id)
    provider_messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *transcript,
    ]

    reason = "completed"
    turn = 0
    while turn < max_turns:
        turn += 1
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        stop_reason: str | None = None

        async for event in provider.stream(
            messages=provider_messages, tools=registry.schemas(tier)
        ):
            if isinstance(event, TextDelta):
                text_chunks.append(event.text)
            elif isinstance(event, ToolCall):
                tool_calls.append(event)
                await append_event(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=session_id,
                    event_type="tool-call",
                    payload={"id": event.id, "name": event.name, "args": event.args},
                )
            elif isinstance(event, Finish):
                stop_reason = event.stop_reason

        assistant_text = "".join(text_chunks)
        if assistant_text:
            await _persist_message(
                session,
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                role="assistant",
                text=assistant_text,
            )
            await append_event(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
                event_type="text-delta",
                payload={"text": assistant_text},
            )

        # Provider-facing assistant turn: carry tool_calls so the model recognizes
        # its own call and accepts the following role:tool results (OpenAI tool
        # protocol). Feeding results as user messages makes models re-issue calls.
        assistant_pm: dict[str, object] = {"role": "assistant", "content": assistant_text or None}
        if tool_calls:
            assistant_pm["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.args)},
                }
                for tc in tool_calls
            ]
        provider_messages.append(assistant_pm)

        await append_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=session_id,
            event_type="turn.end",
            payload={"turn": turn},
        )

        # Stop-reason gate: only dispatch tools on a structured tool_use stop.
        if stop_reason == "tool_use" and tool_calls:
            for call in tool_calls:
                await _run_tool(
                    session,
                    run,
                    turn,
                    call,
                    registry,
                    provider_messages,
                    decider_user_id=decider_user_id,
                )
            continue
        break
    else:
        reason = "stopped:budget"

    run.status = "succeeded"
    run.settled_at = _now()
    await session.flush()
    await append_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        session_id=session_id,
        event_type="run.settled",
        payload={"reason": reason, "status": run.status},
    )
    return reason
