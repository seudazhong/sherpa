"""Schedule REST endpoints (api.md §4.4). Thin adapter over app.services.schedules."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import Schedule as ScheduleSchema
from app.api.schemas import ScheduleCancel, ScheduleCreate, SchedulePage
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.services import CallerContext, ServiceError
from app.services import schedules as svc

router = APIRouter(tags=["schedules"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


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
