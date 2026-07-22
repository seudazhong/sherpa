"""Session + message REST surface (api.md §3.2, §4.2, §10.1).

- POST  /sessions                     create a web:chat session (Session + CSRF)
- GET   /sessions                     Session Library browse (Session)
- PATCH /sessions/{id}/title          rename (Session + CSRF)
- GET   /sessions/{id}/resume-state   truthful resume preflight (Session)
- GET   /sessions/{id}/timeline       messages around a typed anchor (Session)
- POST  /sessions/{id}/recover        state-specific reconciliation (Session + CSRF)
- GET   /sessions/{id}/messages       redacted transcript page (Session)

All queries are scoped by tenant AND user (ADR-029); resources outside the
authenticated owner return 404 (never 403), per §2.1.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    MessagePage,
    PublicMessage,
    PublicMessagePart,
    RecoverRequest,
    ResumeStateResponse,
    SessionCreate,
    SessionMatch,
    SessionPage,
    SessionSummary,
    SessionTitleUpdate,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import EventJournal, Message, Part
from app.models import Session as SessionModel
from app.services import CallerContext, ServiceError
from app.services import sessions as svc

router = APIRouter(tags=["sessions"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


def _summary(view: svc.SessionView) -> SessionSummary:
    s = view.session
    match = None
    if view.match is not None:
        match = SessionMatch(
            kind=view.match.kind,  # type: ignore[arg-type]
            snippet=view.match.snippet,
            anchor_kind=view.match.anchor_kind,  # type: ignore[arg-type]
            anchor_id=view.match.anchor_id,
            additional_matches=view.match.additional_matches,
        )
    return SessionSummary(
        id=s.id,
        tenant_id=s.tenant_id,
        channel=s.channel,
        umo_key=s.umo_key,
        title=s.title,
        resume_state=view.resume_state,  # type: ignore[arg-type]
        latest_run_state=view.latest_run_state,  # type: ignore[arg-type]
        last_message_preview=view.last_message_preview,
        last_activity_at=s.last_activity_at,
        created_at=s.created_at,
        updated_at=s.updated_at,
        match=match,
    )


async def _session_tail(db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID) -> int:
    val = await db.scalar(
        select(func.coalesce(func.max(EventJournal.session_seq), 0)).where(
            EventJournal.tenant_id == tenant_id, EventJournal.session_id == session_id
        )
    )
    return int(val or 0)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    body: SessionCreate,
    response: Response,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SessionSummary:
    session_id = uuid.uuid4()
    sess = SessionModel(
        tenant_id=ctx.tenant_id,
        id=session_id,
        user_id=ctx.user_id,
        umo_key=f"web:chat:{session_id}",
        channel="web",
        channel_installation_id="local",
        scope_type="chat",
        external_scope_id=str(session_id),
        status="open",
        title=body.title,
    )
    db.add(sess)
    await db.flush()
    await db.refresh(sess, ["created_at", "updated_at"])
    view = await svc.get_view(db, _caller(ctx), session_id)
    await db.commit()
    return _summary(view)


@router.get("/sessions")
async def list_sessions(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    query: str | None = None,
    session_status: Annotated[str | None, Query(alias="status")] = None,
    channel: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SessionPage:
    # Non-empty query = content search (P1); empty = recent browse (P0).
    try:
        if query and query.strip():
            page = await svc.search_sessions(db, _caller(ctx), query, limit=limit)
        else:
            page = await svc.browse(
                db,
                _caller(ctx),
                status=session_status,
                channel=channel,
                cursor=cursor,
                limit=limit,
            )
    except ServiceError as e:
        raise _http(e) from None
    return SessionPage(items=[_summary(v) for v in page.items], next_cursor=page.next_cursor)


@router.patch("/sessions/{session_id}/title")
async def rename_session(
    session_id: uuid.UUID,
    body: SessionTitleUpdate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SessionSummary:
    try:
        view = await svc.rename(db, _caller(ctx), session_id, body.title)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _summary(view)


@router.get("/sessions/{session_id}/resume-state")
async def resume_state(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ResumeStateResponse:
    try:
        view = await svc.get_view(db, _caller(ctx), session_id)
    except ServiceError as e:
        raise _http(e) from None
    tail = await _session_tail(db, ctx.tenant_id, session_id)
    return ResumeStateResponse(
        session_id=session_id,
        resume_state=view.resume_state,  # type: ignore[arg-type]
        latest_run_state=view.latest_run_state,  # type: ignore[arg-type]
        live=view.live,
        pending_approval_id=view.pending_approval_id,
        unresolved_effect_id=view.unresolved_effect_id,
        events_url=f"/sessions/{session_id}/events?cursor={tail}",
    )


@router.get("/sessions/{session_id}/timeline")
async def session_timeline(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    anchor_kind: Annotated[str, Query()] = "session",
    anchor_id: Annotated[str, Query()] = "",
    before_turns: Annotated[int, Query(ge=0, le=100)] = 20,
    after_turns: Annotated[int, Query(ge=0, le=100)] = 20,
) -> MessagePage:
    try:
        msgs, _center = await svc.timeline(
            db,
            _caller(ctx),
            session_id,
            anchor_kind=anchor_kind,
            anchor_id=anchor_id,
            before_turns=before_turns,
            after_turns=after_turns,
        )
    except ServiceError as e:
        raise _http(e) from None
    items = await _hydrate(db, ctx.tenant_id, session_id, msgs)
    tail = await _session_tail(db, ctx.tenant_id, session_id)
    return MessagePage(items=items, next_cursor=None, event_cursor=str(tail))


@router.post("/sessions/{session_id}/recover", status_code=status.HTTP_202_ACCEPTED)
async def recover_session(
    session_id: uuid.UUID,
    body: RecoverRequest,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ResumeStateResponse:
    try:
        view = await svc.recover(db, _caller(ctx), session_id, body.action)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    tail = await _session_tail(db, ctx.tenant_id, session_id)
    return ResumeStateResponse(
        session_id=session_id,
        resume_state=view.resume_state,  # type: ignore[arg-type]
        latest_run_state=view.latest_run_state,  # type: ignore[arg-type]
        live=view.live,
        pending_approval_id=view.pending_approval_id,
        unresolved_effect_id=view.unresolved_effect_id,
        events_url=f"/sessions/{session_id}/events?cursor={tail}",
    )


async def _hydrate(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    msgs: list[Message],
) -> list[PublicMessage]:
    parts_by_msg: dict[uuid.UUID, list[PublicMessagePart]] = {}
    if msgs:
        ids = [m.id for m in msgs]
        parts = (
            (
                await db.execute(
                    select(Part)
                    .where(Part.tenant_id == tenant_id, Part.message_id.in_(ids))
                    .order_by(Part.ordinal)
                )
            )
            .scalars()
            .all()
        )
        for p in parts:
            parts_by_msg.setdefault(p.message_id, []).append(
                PublicMessagePart(kind=p.kind, text=str(p.content_redacted.get("text", "")))  # type: ignore[arg-type]
            )
    return [
        PublicMessage(
            id=m.id,
            session_id=session_id,
            seq=m.seq,
            role=m.role,  # type: ignore[arg-type]
            parts=parts_by_msg.get(m.id, []),
            run_id=m.run_id,
            created_at=m.created_at,
        )
        for m in msgs
    ]


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagePage:
    owner = await db.scalar(
        select(SessionModel.user_id).where(
            SessionModel.tenant_id == ctx.tenant_id, SessionModel.id == session_id
        )
    )
    if owner is None or owner != ctx.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    after_seq = 0
    if cursor:
        if not cursor.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad_cursor")
        after_seq = int(cursor)

    msgs = list(
        (
            await db.execute(
                select(Message)
                .where(
                    Message.tenant_id == ctx.tenant_id,
                    Message.session_id == session_id,
                    Message.role.in_(("user", "assistant")),
                    Message.seq > after_seq,
                )
                .order_by(Message.seq)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    next_cursor = None
    if len(msgs) > limit:
        next_cursor = str(msgs[limit - 1].seq)
        msgs = msgs[:limit]

    items = await _hydrate(db, ctx.tenant_id, session_id, msgs)
    tail = await _session_tail(db, ctx.tenant_id, session_id)
    return MessagePage(items=items, next_cursor=next_cursor, event_cursor=str(tail))
