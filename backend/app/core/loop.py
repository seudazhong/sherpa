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
import uuid
from collections import defaultdict

from sqlalchemy import func, select

from app.effects import begin_invocation, mark_running, settle_failed, settle_succeeded
from app.events import append_event
from app.models import Message, Part, Run
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
) -> None:
    key = f"tool:{run.id}:{turn}:{call.id}"
    handle = await begin_invocation(
        session,
        tenant_id=run.tenant_id,
        run_id=run.id,
        effect_name=call.name,
        idempotency_key=key,
        effect_class="read_only",
        retry_policy="transient_before_dispatch",
        args=call.args,
        turn_seq=turn,
    )
    await mark_running(session, run.tenant_id, handle.invocation_id)
    ok = True
    try:
        result = await registry.get(call.name).execute(call.args)
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
    provider_messages.append({"role": "user", "content": f"[tool:{call.name}] {output}"})


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
            provider_messages.append({"role": "assistant", "content": assistant_text})
            await append_event(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
                event_type="text-delta",
                payload={"text": assistant_text},
            )

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
                await _run_tool(session, run, turn, call, registry, provider_messages)
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
