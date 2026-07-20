"""Candidate inbox REST endpoints (api.md §3.3). Thin adapter over
`app.services.candidates` (ADR-023): parse HTTP → build a `CallerContext(actor=
"user")` → call the shared capability layer → commit → map `ServiceError` to an
HTTP status. Todo endpoints live in `app.api.todos`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    Candidate as CandidateSchema,
)
from app.api.schemas import (
    CandidateAccept,
    CandidateAcceptance,
    CandidateDismiss,
    CandidateEdit,
    CandidatePage,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.services import CallerContext, ServiceError
from app.services import candidates as svc

router = APIRouter(tags=["candidates"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


@router.get("/candidates")
async def list_candidates(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str, Query(alias="status")] = "pending",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CandidatePage:
    try:
        return await svc.list_candidates(
            db, _caller(ctx), status_filter=status_filter, cursor=cursor, limit=limit
        )
    except ServiceError as e:
        raise _http(e) from None


@router.post("/candidates/{candidate_id}/accept", status_code=status.HTTP_201_CREATED)
async def accept_candidate(
    candidate_id: uuid.UUID,
    body: CandidateAccept,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateAcceptance:
    try:
        result = await svc.accept_candidate(
            db, _caller(ctx), candidate_id=candidate_id, if_version=body.if_version
        )
        await db.commit()
        return result
    except ServiceError as e:
        raise _http(e) from None


@router.post("/candidates/{candidate_id}/edit", status_code=status.HTTP_201_CREATED)
async def edit_candidate(
    candidate_id: uuid.UUID,
    body: CandidateEdit,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateAcceptance:
    try:
        result = await svc.edit_candidate(
            db,
            _caller(ctx),
            candidate_id=candidate_id,
            if_version=body.if_version,
            title=body.title,
            description=body.description,
            due_at=body.due_at,
            priority=body.priority,
        )
        await db.commit()
        return result
    except ServiceError as e:
        raise _http(e) from None


@router.post("/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(
    candidate_id: uuid.UUID,
    body: CandidateDismiss,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateSchema:
    try:
        result = await svc.dismiss_candidate(
            db,
            _caller(ctx),
            candidate_id=candidate_id,
            if_version=body.if_version,
            reason=body.reason,
        )
        await db.commit()
        return result
    except ServiceError as e:
        raise _http(e) from None
