"""Project per-run telemetry from the durable spine into `traces` + session rollups.

Called once when a run settles (in the worker, same transaction as the loop). When
OTEL is enabled the loop journals per-call `model.request`/`model.response` events
(events §2.7); this projection derives real token totals + one `generations` row
per model call from them. Without those events (default, or the mock provider) it
falls back to estimating tokens from persisted transcript text (~4 chars/token).
Cost is zero until a per-model price table exists.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Generation, Message, Part, Run, Trace
from app.models import Session as SessionModel

_TRACE_STATUS = {
    "queued": "running",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "needs_reconciliation": "failed",
}

# generations.purpose is a fixed enum (web_chat/candidate_extraction/digest). Only
# interactive chat runs map cleanly here; other run kinds still get real tokens on
# the trace + model.* events but no generation row (would need an enum extension).
_GENERATION_PURPOSE = {"web_chat": "web_chat"}


@dataclasses.dataclass
class _ModelCall:
    call_index: int
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    error_type: str | None = None
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None


def _estimate_tokens(chars: int) -> int:
    return max(0, math.ceil(chars / 4))


async def _collect_model_calls(
    session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> list[_ModelCall]:
    """Merge the run's model.request/response debug events into per-call records."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT event_type, payload_redacted AS payload, occurred_at "
                    "FROM event_journal "
                    "WHERE tenant_id = :tid AND run_id = :rid "
                    "AND event_type IN ('model.request', 'model.response') "
                    "ORDER BY run_seq"
                ),
                {"tid": tenant_id, "rid": run_id},
            )
        )
        .mappings()
        .all()
    )
    by_index: dict[int, _ModelCall] = {}
    for r in rows:
        payload = r["payload"] or {}
        idx = int(payload.get("call_index", 0))
        call = by_index.setdefault(idx, _ModelCall(call_index=idx))
        if r["event_type"] == "model.request":
            call.provider = payload.get("provider")
            call.model = payload.get("model")
            call.input_tokens = payload.get("input_tokens")
            call.started_at = r["occurred_at"]
        else:
            call.model = call.model or payload.get("model")
            call.output_tokens = payload.get("output_tokens")
            call.latency_ms = payload.get("latency_ms")
            call.error_type = payload.get("error_type")
            call.completed_at = r["occurred_at"]

    calls = [by_index[k] for k in sorted(by_index)]
    for call in calls:
        started = call.started_at or call.completed_at
        completed = call.completed_at or call.started_at
        if started is None:
            started = completed = datetime.datetime.now(datetime.UTC)
        if completed is None or completed < started:
            completed = started
        call.started_at, call.completed_at = started, completed
    return calls


async def _role_chars(
    session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID, role: str
) -> int:
    mids = (
        (
            await session.execute(
                select(Message.id).where(
                    Message.tenant_id == tenant_id,
                    Message.run_id == run_id,
                    Message.role == role,
                )
            )
        )
        .scalars()
        .all()
    )
    if not mids:
        return 0
    contents = (
        (
            await session.execute(
                select(Part.content_redacted).where(
                    Part.tenant_id == tenant_id, Part.message_id.in_(mids)
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(len(str(c.get("text", ""))) for c in contents)


async def project_run_trace(
    session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> uuid.UUID:
    """Create the run's trace and advance the session token/cost rollups."""
    run = await session.get(Run, (tenant_id, run_id))
    if run is None:
        raise ValueError(f"run not found: {run_id}")
    sess = (
        await session.get(SessionModel, (tenant_id, run.session_id))
        if run.session_id is not None
        else None
    )

    # Prefer real per-call usage journaled by the loop (OTEL on); else estimate.
    calls = await _collect_model_calls(session, tenant_id, run_id)
    real_usage = any(c.input_tokens is not None or c.output_tokens is not None for c in calls)
    if real_usage:
        input_tokens = sum(c.input_tokens or 0 for c in calls)
        output_tokens = sum(c.output_tokens or 0 for c in calls)
    else:
        input_tokens = _estimate_tokens(await _role_chars(session, tenant_id, run_id, "user"))
        output_tokens = _estimate_tokens(await _role_chars(session, tenant_id, run_id, "assistant"))
    cost = Decimal("0")

    trace_id = uuid.uuid4()
    session.add(
        Trace(
            tenant_id=tenant_id,
            id=trace_id,
            run_id=run_id,
            session_id=run.session_id,
            user_id=sess.user_id if sess is not None else None,
            trace_kind=run.run_kind,
            status=_TRACE_STATUS.get(run.status, "succeeded"),
            tags={
                "provider": settings.provider_kind,
                "model": settings.provider_model if settings.provider_kind != "mock" else "mock-v1",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": "0",
            },
            started_at=run.started_at or run.created_at,
            ended_at=run.settled_at,
        )
    )
    await session.flush()

    # One generations row per model call, with real usage (events §2.7). Only run
    # kinds mapping to a valid purpose (interactive chat) get rows; others still
    # carry real tokens on the trace above.
    purpose = _GENERATION_PURPOSE.get(run.run_kind)
    if purpose is not None and calls:
        for c in calls:
            assert c.started_at is not None and c.completed_at is not None
            session.add(
                Generation(
                    tenant_id=tenant_id,
                    id=uuid.uuid4(),
                    trace_id=trace_id,
                    run_id=run_id,
                    extraction_id=None,
                    purpose=purpose,
                    provider=c.provider or settings.provider_kind,
                    model=c.model or "unknown",
                    prompt_version="v1",
                    response_schema_version=None,
                    status="failed" if c.error_type else "succeeded",
                    attempt=1,
                    input_tokens=c.input_tokens or 0,
                    output_tokens=c.output_tokens or 0,
                    cached_input_tokens=0,
                    cost_usd=Decimal("0"),
                    latency_ms=c.latency_ms,
                    started_at=c.started_at,
                    completed_at=c.completed_at,
                )
            )
        await session.flush()

    if sess is not None:
        sess.input_tokens_rollup = (sess.input_tokens_rollup or 0) + input_tokens
        sess.output_tokens_rollup = (sess.output_tokens_rollup or 0) + output_tokens
        sess.cost_usd_rollup = (sess.cost_usd_rollup or Decimal("0")) + cost
        await session.flush()

    return trace_id
