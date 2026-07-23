"""Schedule REST endpoints (api.md §4.5). Thin adapter over app.services.schedules."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import Schedule as ScheduleSchema
from app.api.schemas import (
    ScheduleCancel,
    ScheduleCreate,
    ScheduleFiringItem,
    ScheduleFiringPage,
    SchedulePage,
    ScheduleStatusUpdate,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import ScheduleFiring as ScheduleFiringModel
from app.services import CallerContext, ServiceError
from app.services import schedules as svc

router = APIRouter(tags=["schedules"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


def _firing_item(row: ScheduleFiringModel) -> ScheduleFiringItem:
    return ScheduleFiringItem(
        id=row.id,
        schedule_id=row.schedule_id,
        scheduled_for=row.scheduled_for,
        status=row.status,
        delivery_outcome=row.delivery_outcome,
        run_id=row.run_id,
        settled_at=row.settled_at,
        created_at=row.created_at,
    )


@router.post("/schedules", status_code=201)
async def create_schedule(
    body: ScheduleCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduleSchema:
    try:
        sched = await svc.create_schedule(
            db,
            _caller(ctx),
            kind=body.kind,
            name=body.name,
            delivery_channel=body.delivery_channel,
            timezone=body.timezone,
            local_time=body.local_time,
            todo_id=body.todo_id,
            reminder_kind=body.reminder_kind,
            next_fire_at=body.next_fire_at,
            cadence_kind=body.cadence_kind,
            cron_expr=body.cron_expr,
            interval_seconds=body.interval_seconds,
            weekly_days=body.weekly_days,
            monthly_day=body.monthly_day,
            prompt=body.prompt,
        )
        await db.commit()
        return sched
    except ServiceError as e:
        raise _http(e) from None


@router.get("/schedules")
async def list_schedules(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SchedulePage:
    return await svc.list_schedules(db, _caller(ctx))


@router.post("/schedules/{schedule_id}/cancel")
async def cancel_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleCancel,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduleSchema:
    try:
        sched = await svc.cancel_schedule(
            db, _caller(ctx), schedule_id=schedule_id, if_version=body.if_version
        )
        await db.commit()
        return sched
    except ServiceError as e:
        raise _http(e) from None


@router.post("/schedules/{schedule_id}/status")
async def set_status(
    schedule_id: uuid.UUID,
    body: ScheduleStatusUpdate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduleSchema:
    try:
        sched = await svc.set_status(
            db,
            _caller(ctx),
            schedule_id=schedule_id,
            if_version=body.if_version,
            status=body.status,
        )
        await db.commit()
        return sched
    except ServiceError as e:
        raise _http(e) from None


@router.post("/schedules/{schedule_id}/run-now", status_code=202)
async def run_now(
    schedule_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduleFiringItem:
    try:
        firing = await svc.run_now(db, _caller(ctx), schedule_id=schedule_id)
        await db.commit()
        return _firing_item(firing)
    except ServiceError as e:
        raise _http(e) from None


@router.get("/schedules/{schedule_id}/firings")
async def list_firings(
    schedule_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ScheduleFiringPage:
    try:
        rows = await svc.list_firings(db, _caller(ctx), schedule_id=schedule_id, limit=limit)
    except ServiceError as e:
        raise _http(e) from None
    return ScheduleFiringPage(items=[_firing_item(r) for r in rows], next_cursor=None)
