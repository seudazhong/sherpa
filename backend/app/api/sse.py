"""Session event stream (SSE) endpoint (contracts/api.md §5, events-and-effects §3).

Authenticated by the session cookie (no CSRF for GET/SSE); the tenant comes from
the authenticated session and the target session must belong to it (else 404).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.auth import current_session
from app.db import SessionLocal
from app.events.stream import session_event_stream
from app.models import Session as SessionModel

router = APIRouter()


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: uuid.UUID, request: Request, cursor: int = 0
) -> StreamingResponse:
    data = await current_session(request)
    async with SessionLocal() as db:
        exists = await db.scalar(
            select(SessionModel.id).where(
                SessionModel.tenant_id == data.tenant_id, SessionModel.id == session_id
            )
        )
    if exists is None:
        raise HTTPException(status_code=404, detail="session not found")

    # Last-Event-ID (reconnect) takes precedence over the initial ?cursor=.
    last_event_id = request.headers.get("last-event-id")
    after_seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else cursor

    return StreamingResponse(
        session_event_stream(data.tenant_id, session_id, after_seq=after_seq, live=True),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
