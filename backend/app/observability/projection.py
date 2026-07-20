"""Project per-run telemetry from the durable spine into `traces` + session rollups.

Called once when a run settles (in the worker, same transaction as the loop). v1
uses the mock provider, so token counts are estimated from persisted transcript
text (~4 chars/token) and cost is zero; a real provider will report usage that
this projection prefers. `generations`/`audit_receipts` land in M2 (they depend
on M2 tables).
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Message, Part, Run, Trace
from app.models import Session as SessionModel

_TRACE_STATUS = {
    "queued": "running",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "needs_reconciliation": "failed",
}


def _estimate_tokens(chars: int) -> int:
    return max(0, math.ceil(chars / 4))


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

    if sess is not None:
        sess.input_tokens_rollup = (sess.input_tokens_rollup or 0) + input_tokens
        sess.output_tokens_rollup = (sess.output_tokens_rollup or 0) + output_tokens
        sess.cost_usd_rollup = (sess.cost_usd_rollup or Decimal("0")) + cost
        await session.flush()

    return trace_id
