"""Session + message REST surface (api.md §3.2, §4.2).

- POST /sessions           create a web:chat session (Session + CSRF)
- GET  /sessions           keyset-paginated session list (Session)
- GET  /sessions/{id}/messages  redacted transcript page (Session)

All queries are tenant-scoped from RequestContext; resources outside the
authenticated tenant return 404 (never 403), per §2.1.
"""

from __future__ import annotations

import base64
import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    MessagePage,
    PublicMessage,
    PublicMessagePart,
    SessionCreate,
    SessionPage,
    SessionSummary,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import EventJournal, Message, Part, Run
from app.models import Session as SessionModel

router = APIRouter(tags=["sessions"])

_RUN_STATE = {
    "queued": "queued",
    "running": "running",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "interrupted",
    "needs_reconciliation": "needs_attention",
}


def _encode_cursor(created_at: datetime.datetime, sid: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{sid}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, sid = raw.split("|", 1)
        return datetime.datetime.fromisoformat(ts), uuid.UUID(sid)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad_cursor") from None


async def _session_tail(db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID) -> int:
    val = await db.scalar(
        select(func.coalesce(func.max(EventJournal.session_seq), 0)).where(
            EventJournal.tenant_id == tenant_id, EventJournal.session_id == session_id
        )
    )
    return int(val or 0)


async def _summary(db: AsyncSession, s: SessionModel) -> SessionSummary:
    latest_status = await db.scalar(
        select(Run.status)
        .where(Run.tenant_id == s.tenant_id, Run.session_id == s.id)
        .order_by(Run.created_at.desc())
        .limit(1)
    )
    latest_state = _RUN_STATE.get(latest_status) if latest_status else None

    preview_mid = await db.scalar(
        select(Message.id)
        .where(Message.tenant_id == s.tenant_id, Message.session_id == s.id)
        .order_by(Message.seq.desc())
        .limit(1)
    )
    preview = None
    if preview_mid is not None:
        content = await db.scalar(
            select(Part.content_redacted).where(
                Part.tenant_id == s.tenant_id, Part.message_id == preview_mid, Part.ordinal == 0
            )
        )
        if content is not None:
            preview = str(content.get("text", ""))[:140]

    return SessionSummary(
        id=s.id,
        tenant_id=s.tenant_id,
        channel="web",
        umo_key=s.umo_key,
        title=None,
        latest_run_state=latest_state,  # type: ignore[arg-type]
        last_message_preview=preview,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


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
    )
    db.add(sess)
    await db.flush()
    await db.refresh(sess, ["created_at", "updated_at"])
    await db.commit()
    summary = await _summary(db, sess)
    # SessionCreate.title is a UI hint; v1 has no title column, so echo it back only.
    return summary.model_copy(update={"title": body.title})


@router.get("/sessions")
async def list_sessions(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SessionPage:
    stmt = (
        select(SessionModel)
        .where(SessionModel.tenant_id == ctx.tenant_id, SessionModel.user_id == ctx.user_id)
        .order_by(SessionModel.created_at.desc(), SessionModel.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        ts, sid = _decode_cursor(cursor)
        stmt = stmt.where(tuple_(SessionModel.created_at, SessionModel.id) < (ts, sid))

    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode_cursor(last.created_at, last.id)
        rows = rows[:limit]
    items = [await _summary(db, s) for s in rows]
    return SessionPage(items=items, next_cursor=next_cursor)


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
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    after_seq = 0
    if cursor:
        if not cursor.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad_cursor")
        after_seq = int(cursor)

    msgs = (
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

    parts_by_msg: dict[uuid.UUID, list[PublicMessagePart]] = {}
    if msgs:
        ids = [m.id for m in msgs]
        parts = (
            (
                await db.execute(
                    select(Part)
                    .where(Part.tenant_id == ctx.tenant_id, Part.message_id.in_(ids))
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

    items = [
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
    tail = await _session_tail(db, ctx.tenant_id, session_id)
    return MessagePage(items=items, next_cursor=next_cursor, event_cursor=str(tail))
