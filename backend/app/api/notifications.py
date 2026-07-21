"""Notifications (web inbox) + user settings endpoints (api.md §4.6).

The web inbox is projected from settled schedule_firings (delivered items plus
surfaced missed/failed/unknown). Settings expose the notification preferences and
are optimistic-concurrency guarded by if_version.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    Notification,
    NotificationPage,
    Settings,
    SettingsPatch,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import Schedule, ScheduleFiring, UserSettings
from app.notifications import ensure_settings

router = APIRouter(tags=["notifications"])


def _settings_schema(row: UserSettings) -> Settings:
    return Settings(
        notifications_enabled=row.notifications_enabled,
        web_enabled=row.web_enabled,
        email_digest_enabled=row.email_digest_enabled,
        timezone=row.timezone,
        quiet_hours_enabled=row.quiet_hours_enabled,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        daily_cap=row.daily_cap,
        version=row.version,
    )


@router.get("/notifications")
async def list_notifications(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NotificationPage:
    rows = (
        await db.execute(
            select(ScheduleFiring, Schedule)
            .join(
                Schedule,
                (Schedule.tenant_id == ScheduleFiring.tenant_id)
                & (Schedule.id == ScheduleFiring.schedule_id),
            )
            .where(
                ScheduleFiring.tenant_id == ctx.tenant_id,
                ScheduleFiring.status == "settled",
            )
            .order_by(ScheduleFiring.scheduled_for.desc())
            .limit(limit)
        )
    ).all()
    items = [
        Notification(
            firing_id=firing.id,
            schedule_id=schedule.id,
            schedule_name=schedule.name,
            channel=schedule.delivery_channel,
            scheduled_for=firing.scheduled_for,
            status=firing.status,
            delivery_outcome=firing.delivery_outcome,
            settled_at=firing.settled_at,
        )
        for firing, schedule in rows
    ]
    return NotificationPage(items=items, next_cursor=None)


@router.get("/settings")
async def get_settings(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Settings:
    row = await ensure_settings(db, ctx.tenant_id, ctx.user_id)
    await db.commit()
    return _settings_schema(row)


@router.patch("/settings")
async def patch_settings(
    body: SettingsPatch,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Settings:
    row = await ensure_settings(db, ctx.tenant_id, ctx.user_id)
    if row.version != body.if_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="version_conflict")
    if body.notifications_enabled is not None:
        row.notifications_enabled = body.notifications_enabled
    if body.web_enabled is not None:
        row.web_enabled = body.web_enabled
    if body.email_digest_enabled is not None:
        row.email_digest_enabled = body.email_digest_enabled
    if body.timezone is not None:
        row.timezone = body.timezone
    if body.quiet_hours_enabled is not None:
        row.quiet_hours_enabled = body.quiet_hours_enabled
    if body.quiet_hours_start is not None:
        row.quiet_hours_start = body.quiet_hours_start
    if body.quiet_hours_end is not None:
        row.quiet_hours_end = body.quiet_hours_end
    if body.daily_cap is not None:
        row.daily_cap = body.daily_cap
    if row.quiet_hours_start == row.quiet_hours_end:
        raise HTTPException(status_code=422, detail="quiet_hours_equal")
    row.version += 1
    await db.commit()
    return _settings_schema(row)
