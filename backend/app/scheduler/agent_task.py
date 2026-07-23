"""Scheduled agent-task dispatch (ADR-031, Phase CRON).

When an ``agent_task`` schedule fires, its pending ``schedule_firings`` slot is
turned into an autonomous run: a durable prompt admission (``run_kind=
'scheduled_task'``) into a dedicated per-schedule session, seeded with the saved
prompt. The firing's slot key deterministically derives the admission
``client_message_id`` so a worker replay is idempotent (``admit_prompt`` returns the
existing run — never a second one). The firing records ``run_id`` and moves to
``running``; result delivery + settle happens when the run settles (CRON.3).

Guardrails (ADR-031): a per-user concurrency cap on in-flight scheduled runs (defer,
do not drop, when exceeded); the min-frequency floor is enforced by the cadence
engine + DB CHECK. External side effects inside the run remain approval-gated — the
run simply pauses at the approval, exactly like an interactive chat.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.admission import admit_prompt
from app.models import Run, Schedule, ScheduleFiring
from app.models import Session as SessionModel

# Stable namespace so a firing slot always maps to the same admission id.
_SLOT_NS = uuid.uuid5(uuid.NAMESPACE_URL, "sherpa/scheduled-task/firing")

_RETRY_AFTER = datetime.timedelta(minutes=1)


async def _ensure_session(session: AsyncSession, schedule: Schedule) -> uuid.UUID:
    """Return the schedule's dedicated session id, creating it once."""
    umo = f"scheduled:{schedule.id}"
    existing = await session.scalar(
        select(SessionModel.id).where(
            SessionModel.tenant_id == schedule.tenant_id, SessionModel.umo_key == umo
        )
    )
    if existing is not None:
        return existing
    sid = uuid.uuid4()
    session.add(
        SessionModel(
            tenant_id=schedule.tenant_id,
            id=sid,
            user_id=schedule.user_id,
            umo_key=umo,
            channel="web",
            channel_installation_id="local",
            scope_type="chat",
            external_scope_id=umo,
            status="open",
            title=schedule.name,
        )
    )
    await session.flush()
    return sid


async def _inflight_scheduled_runs(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    val = await session.scalar(
        select(func.count())
        .select_from(Run)
        .join(
            SessionModel,
            (SessionModel.tenant_id == Run.tenant_id) & (SessionModel.id == Run.session_id),
        )
        .where(
            Run.tenant_id == tenant_id,
            Run.run_kind == "scheduled_task",
            Run.status.in_(("queued", "running")),
            SessionModel.user_id == user_id,
        )
    )
    return int(val or 0)


async def dispatch_due_agent_tasks(
    session: AsyncSession, now: datetime.datetime
) -> list[uuid.UUID]:
    """Admit a run for each due ``agent_task`` firing. Returns run ids to enqueue.

    Caller owns the transaction (commit) and enqueues the returned run ids.
    """
    firings = (
        (
            await session.execute(
                select(ScheduleFiring)
                .join(
                    Schedule,
                    (Schedule.tenant_id == ScheduleFiring.tenant_id)
                    & (Schedule.id == ScheduleFiring.schedule_id),
                )
                .where(
                    ScheduleFiring.status == "pending",
                    ScheduleFiring.available_at <= now,
                    Schedule.kind == "agent_task",
                )
                .order_by(ScheduleFiring.available_at)
                .with_for_update(skip_locked=True, of=ScheduleFiring)
            )
        )
        .scalars()
        .all()
    )

    enqueued: list[uuid.UUID] = []
    for firing in firings:
        if firing.run_id is not None:  # already dispatched (defensive)
            continue
        schedule = await session.get(Schedule, (firing.tenant_id, firing.schedule_id))
        if schedule is None or schedule.kind != "agent_task" or not schedule.prompt:
            continue

        inflight = await _inflight_scheduled_runs(session, firing.tenant_id, schedule.user_id)
        if inflight >= settings.scheduled_task_max_concurrency:
            firing.available_at = now + _RETRY_AFTER  # defer, do not drop
            firing.updated_at = now
            await session.flush()
            continue

        session_id = await _ensure_session(session, schedule)
        client_message_id = uuid.uuid5(_SLOT_NS, firing.firing_key)
        adm = await admit_prompt(
            session,
            tenant_id=firing.tenant_id,
            session_id=session_id,
            user_id=schedule.user_id,
            client_message_id=client_message_id,
            text=schedule.prompt,
            run_kind="scheduled_task",
        )
        firing.run_id = adm.run_id
        firing.status = "running"
        firing.started_at = now
        firing.updated_at = now
        await session.flush()
        if not adm.reused:
            enqueued.append(adm.run_id)

    return enqueued
