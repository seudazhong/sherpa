"""Schedule firing tick: advance-cursor-then-run, at-most-once per slot (ADR-017).

For each due active schedule the tick records a firing keyed by its
(schedule, scheduled_for) slot (unique -> no double-fire) and advances the
schedule's cursor to the next occurrence. A slot that is already stale under a
`skip` misfire policy is recorded as `missed` so it stays visible; otherwise a
`pending` firing is created for the delivery worker (M2 #19). Delivery itself is
not performed here.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Schedule, ScheduleFiring

_MISSED_GRACE = datetime.timedelta(hours=1)


def _slot_key(schedule_id: uuid.UUID, scheduled_for: datetime.datetime) -> str:
    return f"{schedule_id}:{int(scheduled_for.timestamp())}"


def _advance(next_fire_at: datetime.datetime, now: datetime.datetime) -> datetime.datetime:
    """Next occurrence strictly after now (v1: daily cadence)."""
    nxt = next_fire_at
    while nxt <= now:
        nxt += datetime.timedelta(days=1)
    return nxt


async def fire_due_schedules(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]:
    """Create firings for all due active schedules; advance their cursors.

    Returns the ids of firings created by this tick (idempotent: a slot already
    recorded is skipped). Caller owns the transaction.
    """
    due = (
        (
            await session.execute(
                select(Schedule)
                .where(Schedule.status == "active", Schedule.next_fire_at <= now)
                .order_by(Schedule.next_fire_at)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    created: list[uuid.UUID] = []
    for schedule in due:
        scheduled_for = schedule.next_fire_at
        missed = schedule.misfire_policy == "skip" and (now - scheduled_for) > _MISSED_GRACE
        key = _slot_key(schedule.id, scheduled_for)

        values: dict[str, object] = {
            "tenant_id": schedule.tenant_id,
            "id": uuid.uuid4(),
            "schedule_id": schedule.id,
            "firing_key": key,
            "scheduled_for": scheduled_for,
            "delivery_idempotency_key": f"firing:{key}",
            "available_at": now,
        }
        if missed:
            values.update(status="settled", delivery_outcome="missed", settled_at=now)
        else:
            values.update(status="pending")

        stmt = (
            pg_insert(ScheduleFiring)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_schedule_firings_slot")
            .returning(ScheduleFiring.id)
        )
        row = (await session.execute(stmt)).first()
        if row is not None:
            created.append(row[0])

        # advance-cursor: move the schedule past `now` so the slot won't re-fire
        schedule.next_fire_at = _advance(scheduled_for, now)
        schedule.last_fired_at = now
        await session.flush()

    return created
