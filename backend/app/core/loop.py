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
import hashlib
import json
import logging
import uuid

from opentelemetry.trace import Span
from sqlalchemy import func, select

from app.audit import ACTION, record_receipt
from app.config import settings
from app.core.compaction import compact, should_compact
from app.core.history import assemble_provider_history
from app.core.session_context import render_session_context
from app.effects import begin_invocation, mark_running, settle_failed, settle_succeeded
from app.events import append_event
from app.models import Message, Part, Run, Session
from app.observability import genai, get_tracer
from app.permissions import policy as perm_policy
from app.permissions import request_approval
from app.permissions.grants import find_matching_grant
from app.providers import Finish, Provider, TextDelta, ToolCall
from app.services import CallerContext
from app.services import memory as memory_service
from app.services.model_providers import session_supports_vision
from app.tools import FULL, ToolContext, ToolError, ToolRegistry, bound_text, spill_output

logger = logging.getLogger("app.core.loop")

SYSTEM_PROMPT = (
    "You are Sherpa, a careful assistant. Use tools when needed; be concise. "
    "You have a durable memory of the user: when they share a stable fact or "
    "preference worth recalling in future sessions, save it with the memory_user_set "
    "tool (and use memory_user_get / memory_user_list to recall). Any core memory you "
    "already hold about the user is included below when present. "
    "Some actions (like sending email) require approval — they are gated automatically "
    "and the user approves or rejects them inline in this app, so never tell the user to "
    "use an external approval interface or reference IDs; just proceed and let the inline "
    "approval prompt appear."
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _input_digest(messages: list[dict[str, object]]) -> tuple[str, int]:
    """Return (sha-256 hex, char length) of the assembled provider input.

    Lets a model.request event verify/correlate the exact assembled input without
    persisting its text (ADR-033/ADR-019: content is span-only + opt-in).
    """
    canonical = json.dumps(messages, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), len(canonical)


async def _touch_session_activity(  # type: ignore[no-untyped-def]
    session, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    """Advance sessions.last_activity_at so the Session Library can order by recency."""
    sess = await session.get(Session, (tenant_id, session_id))
    if sess is not None:
        sess.last_activity_at = _now()
        await session.flush()


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
    # `execute_tool` span (ADR-033): child of the run's `invoke_agent` span. An
    # error observation marks the span ERROR + agent.tool.success=false; a tool
    # gated on approval leaves success unset (it neither ran nor failed).
    tool_started = _now()
    with get_tracer().start_as_current_span(genai.SPAN_EXECUTE_TOOL) as span:
        genai.set_attrs(
            span,
            {
                genai.OPERATION_NAME: genai.OP_EXECUTE_TOOL,
                genai.SPAN_KIND: genai.KIND_TOOL,
                genai.TOOL_NAME: call.name,
                genai.TOOL_CALL_ID: call.id,
            },
        )
        outcome = await _run_tool_impl(
            session,
            run,
            turn,
            call,
            registry,
            provider_messages,
            decider_user_id=decider_user_id,
        )
        if outcome == "ok":
            genai.record_tool_result(span, success=True)
        elif outcome == "error":
            genai.record_tool_result(span, success=False)
        # "gated" (awaiting approval) → success left unset.

    # One structured line per tool execution (Logs pillar, ADR-033). Unconditional
    # (not gated on OTEL): tool name / outcome / latency, correlated by run. An
    # `error` outcome logs at WARNING so failures stand out in stdout. No content.
    tool_latency_ms = int((_now() - tool_started).total_seconds() * 1000)
    logger.log(
        logging.WARNING if outcome == "error" else logging.INFO,
        "tool call",
        extra={
            "run_id": str(run.id),
            "session_id": str(run.session_id),
            "turn": turn,
            "tool": call.name,
            "call_id": call.id,
            "outcome": outcome,
            "latency_ms": tool_latency_ms,
        },
    )


async def _run_tool_impl(  # type: ignore[no-untyped-def]
    session,
    run: Run,
    turn: int,
    call: ToolCall,
    registry: ToolRegistry,
    provider_messages: list[dict[str, object]],
    *,
    decider_user_id: uuid.UUID | None,
) -> str:
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
        return "error"

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

    # Permission gate (api.md §7.1 ALLOWED): evaluate policy, then dispatch.
    #   allow → execute;  ask → approval envelope (not performed);  deny → refuse.
    # Pre-authorization (ADR-034): a matching owner grant flips `ask` → auto-allow;
    # the action still records its effect + an audit receipt (auto_approved_by_grant).
    decision = perm_policy.evaluate(tool)
    if decision == "ask" and decider_user_id is not None:
        grant = await find_matching_grant(
            session,
            tenant_id=run.tenant_id,
            user_id=decider_user_id,
            tool_name=tool.name,
            args=call.args,
        )
        if grant is None:
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
            observation = (
                f"permission_required: approval requested for {tool.name} "
                f"(correlation {env.correlation_id}); the action was NOT performed and awaits "
                "the user's decision."
            )
            await append_event(
                session,
                tenant_id=run.tenant_id,
                run_id=run.id,
                session_id=run.session_id,
                event_type="permission.asked",
                payload={
                    "id": call.id,
                    "observation": observation,
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
            provider_messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": observation}
            )
            return "gated"
        # Grant matched → auto-allow. Record the pre-authorized decision for audit,
        # then fall through to execute exactly like a normal allow.
        await record_receipt(
            session,
            tenant_id=run.tenant_id,
            receipt_type=ACTION,
            actor_type="system",
            trigger_type="grant",
            action=tool.name,
            outcome="auto_approved",
            run_id=run.id,
            invocation_id=handle.invocation_id,
            subject_type="permission_grant",
            subject_id=grant.id,
            summary={
                "auto_approved_by_grant": True,
                "grant_id": str(grant.id),
                "permission_scope": perm_policy.permission_scope(tool.name),
            },
            reversible=True,
        )
        decision = "allow"

    if decision != "allow":
        # deny, or ask without a decider → refuse; never execute.
        reason = "not_permitted" if decision == "deny" else "approval_required"
        await settle_failed(session, run.tenant_id, handle.invocation_id, error=reason)
        await append_event(
            session,
            tenant_id=run.tenant_id,
            run_id=run.id,
            session_id=run.session_id,
            event_type="tool-error",
            payload={"id": call.id, "name": call.name, "ok": False, "output": f"error: {reason}"},
        )
        provider_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": f"error: {reason}: {tool.name} was not executed",
            }
        )
        return "error"

    await mark_running(session, run.tenant_id, handle.invocation_id)
    tool_ctx = ToolContext(
        tenant_id=run.tenant_id,
        user_id=decider_user_id,
        session_id=run.session_id,
        run_id=run.id,
        invocation_id=handle.invocation_id,
        deadline=run.deadline_at,
        session=session,
    )
    ok = True
    try:
        result = await tool.execute(tool_ctx, call.args)
        bounded = bound_text(result.llm_content)
        output = bounded.text
        spill_ref: str | None = None
        if bounded.truncated:
            spill_ref = spill_output(
                settings.tool_output_root, handle.invocation_id, result.llm_content
            )
            output = (
                f"{bounded.text}\n[full output spilled: {spill_ref} · "
                f"{bounded.original_lines} lines / {bounded.original_bytes} bytes]"
            )
        await settle_succeeded(
            session,
            run.tenant_id,
            handle.invocation_id,
            result={"truncated": bounded.truncated, "spill_ref": spill_ref},
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
    return "ok" if ok else "error"


async def _load_core_memory(  # type: ignore[no-untyped-def]
    session, tenant_id: uuid.UUID, user_id: uuid.UUID | None
) -> str:
    """Render the user's private core memory for the system context (docs/04 铁律#6).

    Durable, always-available facts the agent keeps about the user; injected so it
    recalls them without an explicit tool call. Empty when there is no user or no
    stored memory.
    """
    if user_id is None:
        return ""
    rows = await memory_service.list_memory(
        session, CallerContext(tenant_id=tenant_id, user_id=user_id, actor="agent")
    )
    if not rows:
        return ""
    lines = "\n".join(f"- {r.memory_key}: {r.value_text}" for r in rows)
    return "Durable facts you remember about the user (core memory):\n" + lines


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
    # Root `invoke_agent` span (ADR-033): a derived diagnostic surface over the
    # journal. No-op + zero overhead when OTEL is disabled. Child `chat` /
    # `execute_tool` spans parent to this one via the active OTel context.
    with get_tracer().start_as_current_span(genai.SPAN_INVOKE_AGENT) as root:
        genai.set_attrs(
            root,
            {
                genai.OPERATION_NAME: genai.OP_INVOKE_AGENT,
                genai.SPAN_KIND: genai.KIND_AGENT,
                genai.AGENT_RUN_ID: str(run.id),
                genai.AGENT_SESSION_ID: str(run.session_id),
                genai.AGENT_TENANT_ID: str(run.tenant_id),
            },
        )
        return await _run_agent_loop(
            session,
            run=run,
            provider=provider,
            registry=registry,
            tier=tier,
            max_turns=max_turns,
            root_span=root,
        )


async def _run_agent_loop(  # type: ignore[no-untyped-def]
    session,
    *,
    run: Run,
    provider: Provider,
    registry: ToolRegistry,
    tier: str = FULL,
    max_turns: int = 25,
    root_span: Span,
) -> str:
    tenant_id, run_id, session_id = run.tenant_id, run.id, run.session_id
    assert session_id is not None  # execute_run guarantees a session-bound run
    decider_user_id = await session.scalar(
        select(Session.user_id).where(Session.tenant_id == tenant_id, Session.id == session_id)
    )

    # Note: the run row is intentionally NOT written here. The worker claims the
    # run + lease in an independent committed transaction (app.core.lease) before
    # calling this, so a mid-run heartbeat is never blocked by this long
    # transaction's row lock. Direct callers (tests) simply see the run settle at
    # the end. We still emit run.started as the first session event.
    await append_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        session_id=session_id,
        event_type="run.started",
        payload={"run_kind": run.run_kind},
    )
    await _touch_session_activity(session, tenant_id, session_id)

    transcript = await assemble_provider_history(
        session,
        tenant_id,
        session_id,
        supports_vision=await session_supports_vision(
            session, tenant_id=tenant_id, session_id=session_id
        ),
    )
    core_memory = await _load_core_memory(session, tenant_id, decider_user_id)
    # Layered system message (docs/04): global prefix → per-user memory → per-session
    # ambient context. Ordering is by how widely each layer is shared, so the cacheable
    # prefix stays byte-stable across a user's sessions and only the tail differs.
    ambient = await render_session_context(session, tenant_id=tenant_id, session_id=session_id)
    system_content = "\n\n".join(p for p in (SYSTEM_PROMPT, core_memory, ambient) if p)
    provider_messages: list[dict[str, object]] = [
        {"role": "system", "content": system_content},
        *transcript,
    ]

    reason = "completed"
    turn = 0
    while turn < max_turns:
        turn += 1
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        stop_reason: str | None = None

        # Compaction guardrail (docs/04): keep head + recent, verify-shrank, no
        # orphan tool results. Runs at the top of a turn so the window fed to the
        # provider stays bounded; session identity is unchanged.
        if should_compact(provider_messages, settings.compaction_char_budget):
            result = compact(
                provider_messages,
                keep_head=settings.compaction_keep_head,
                keep_recent=settings.compaction_keep_recent,
            )
            if result.shrank:
                provider_messages = result.messages
                await append_event(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=session_id,
                    event_type="compaction",
                    payload={
                        "before": result.before,
                        "after": result.after,
                        "omitted": result.omitted,
                    },
                )

        # `chat` span (ADR-033): one per provider.stream call. Span duration is
        # the model latency; finish_reasons carry the structured stop reason.
        schemas = registry.schemas(tier)
        model_name = getattr(provider, "_model", None)
        call_started = _now()
        call_input_tokens: int | None = None
        call_output_tokens: int | None = None
        with get_tracer().start_as_current_span(genai.SPAN_CHAT) as chat_span:
            genai.set_attrs(
                chat_span,
                {
                    genai.OPERATION_NAME: genai.OP_CHAT,
                    genai.SPAN_KIND: genai.KIND_LLM,
                    genai.SYSTEM: provider.name,
                    genai.REQUEST_MODEL: model_name,
                },
            )
            async for event in provider.stream(messages=provider_messages, tools=schemas):
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
                    call_input_tokens = event.input_tokens
                    call_output_tokens = event.output_tokens
            genai.set_attrs(
                chat_span,
                {
                    genai.RESPONSE_FINISH_REASONS: (stop_reason,)
                    if stop_reason is not None
                    else None,
                    genai.USAGE_INPUT_TOKENS: call_input_tokens,
                    genai.USAGE_OUTPUT_TOKENS: call_output_tokens,
                },
            )
            # Full assembled prompt + response as OpenInference attrs (OBSB.1),
            # for the Phoenix UI. Opt-in (PII), redacted + bounded; off by default.
            if settings.otel_capture_message_content:
                genai.capture_llm_io(
                    chat_span,
                    messages=provider_messages,
                    tools=schemas,
                    output_text="".join(text_chunks),
                    output_tool_calls=tool_calls,
                )

        latency_ms = int((_now() - call_started).total_seconds() * 1000)
        output_chars = sum(len(c) for c in text_chunks)
        model_label = model_name or provider.name

        # One structured line per LLM call (Logs pillar, ADR-033). Emitted
        # unconditionally — NOT gated on OTEL — so stdout is useful by default:
        # model/tokens/finish/latency, correlated by run/session. No content.
        logger.info(
            "llm call",
            extra={
                "run_id": str(run_id),
                "session_id": str(session_id),
                "turn": turn,
                "provider": provider.name,
                "model": model_label,
                "input_tok": call_input_tokens,
                "output_tok": call_output_tokens,
                "finish_reason": stop_reason,
                "tool_calls": len(tool_calls),
                "latency_ms": latency_ms,
            },
        )

        # Durable, redacted per-call record (events §2.7, durability=debug) — no
        # prompt/tool content, only a sha-256 digest + counts. Gated on OTEL so the
        # journal is untouched by default; project_run_trace derives generations +
        # real token totals from these. Written post-call so real usage is present.
        if settings.otel_enabled:
            digest, input_chars = _input_digest(provider_messages)
            await append_event(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
                event_type="model.request",
                payload={
                    "call_index": turn,
                    "provider": provider.name,
                    "model": model_label,
                    "prompt_version": None,
                    "input_tokens": call_input_tokens,
                    "input_chars": input_chars,
                    "input_digest": digest,
                    "tools_offered": len(schemas),
                    "sampled": True,
                },
                durability="debug",
            )
            await append_event(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
                event_type="model.response",
                payload={
                    "call_index": turn,
                    "model": model_label,
                    "finish_reason": stop_reason or "stop",
                    "output_tokens": call_output_tokens,
                    "output_chars": output_chars,
                    "tool_calls": len(tool_calls),
                    "latency_ms": latency_ms,
                    "error_type": None,
                },
                durability="debug",
            )

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
    run.started_at = run.started_at or _now()
    run.settled_at = _now()
    run.lease_expires_at = None
    await session.flush()
    await _touch_session_activity(session, tenant_id, session_id)
    # Reindex the session's search projection now that assistant turns + tool
    # events are persisted (ADR-029 P1). Pure function of canonical rows.
    from app.search import reindex_session

    await reindex_session(session, tenant_id, session_id)
    await append_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        session_id=session_id,
        event_type="run.settled",
        payload={"reason": reason, "status": run.status},
    )
    genai.set_attrs(
        root_span,
        {genai.AGENT_LOOP_COUNT: turn, genai.AGENT_STOP_REASON: reason},
    )
    return reason
