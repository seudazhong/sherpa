"""Durable prompt admission endpoint (api.md §4, ADR-005).

`POST /sessions/{id}/prompt`: persist the user message + a queued run in one
transaction, commit, then enqueue the run job and return `202`. The core loop
runs in the worker, never in the web process. Auth/CSRF is added in M1 #10; v1
is single-user and resolves tenant/user from the session row.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue
from app.api.schemas import PromptAdmission, PromptRequest
from app.auth import RequestContext, require_csrf
from app.core.admission import PromptConflict, admit_prompt
from app.db import get_session
from app.models import Session as SessionModel

router = APIRouter()


@router.post("/sessions/{session_id}/prompt", status_code=status.HTTP_202_ACCEPTED)
async def post_prompt(
    session_id: uuid.UUID,
    body: PromptRequest,
    response: Response,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PromptAdmission:
    owner = await db.scalar(
        select(SessionModel.user_id).where(
            SessionModel.tenant_id == ctx.tenant_id, SessionModel.id == session_id
        )
    )
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    try:
        adm = await admit_prompt(
            db,
            tenant_id=ctx.tenant_id,
            session_id=session_id,
            user_id=ctx.user_id,
            client_message_id=body.client_message_id,
            text=body.text,
        )
    except PromptConflict:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="idempotency_conflict"
        ) from None

    await db.commit()
    if not adm.reused:
        await queue.enqueue_run(adm.run_id)

    response.headers["Location"] = f"/sessions/{session_id}"
    return PromptAdmission(
        session_id=adm.session_id,
        message_id=adm.message_id,
        run_id=adm.run_id,
        admitted_seq=adm.admitted_seq,
        state="queued",
        event_cursor=adm.event_cursor,
        events_url=f"/sessions/{session_id}/events?cursor={adm.event_cursor}",
    )
