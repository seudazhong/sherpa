"""IM + agentic-email inbound channels REST (ADR-026 QQ, ADR-027 email; milestones 4–5).

Per channel:

- ``POST /channels/{email}/webhook`` — inbound email events, authenticated by a
  Svix HMAC-SHA256 body signature (not a cookie). QQ has no webhook: it arrives
  over the botpy WebSocket in the worker (ADR-028). A normal message is admitted as
  a durable run; an ``approve``/``reject`` reply resolves the pending approval
  envelope over that channel (reusing the v1 approval base, ADR-020). Both channels
  share one generic inbound path (``app.channels.handle_inbound``).
- ``GET /channels`` — owner-authed status projection + recent threads (drives the
  Messaging page + its "not configured" affordances).
- ``POST /channels/{qq,email}/simulate`` — owner-authed test hook injecting an
  inbound message, so the human verification lane works without a live bot/mailbox.
  ``GET /channels/threads/{id}`` reads a thread transcript (any channel).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, ensure_owner, require_context, require_csrf
from app.channels import (
    build_email_channel_client,
    build_qq_client,
    handle_inbound,
)
from app.channels.email import verify_svix_signature
from app.config import settings
from app.db import get_session
from app.models import ApprovalEnvelope, Message, Part
from app.models import Session as SessionModel
from app.services import channels as chan_svc

router = APIRouter(tags=["channels"])

_CHANNEL = "qq"
_EMAIL = "email"


async def _qq_notify(external_id: str, text: str) -> None:
    await build_qq_client().send_private(external_id, text)


async def _email_notify(external_id: str, text: str) -> None:
    await build_email_channel_client().send(to=external_id, subject="Sherpa", text=text)


# --------------------------------------------------------------------------- #
# Inbound email webhook (Svix signature authenticated) — agentic email.        #
# --------------------------------------------------------------------------- #


def _parse_email_address(raw: str) -> str:
    """Extract the bare address from ``Name <addr>`` or ``addr``."""
    raw = raw.strip()
    if "<" in raw and ">" in raw:
        return raw[raw.index("<") + 1 : raw.index(">")].strip().lower()
    return raw.lower()


@router.post("/channels/email/webhook")
async def email_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if settings.email_kind != "agentmail":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email_disabled")
    raw = await request.body()
    if not verify_svix_signature(
        settings.agentmail_webhook_secret,
        raw,
        svix_id=request.headers.get("svix-id"),
        svix_timestamp=request.headers.get("svix-timestamp"),
        svix_signature=request.headers.get("svix-signature"),
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")

    event = await request.json()
    if str(event.get("event_type", "")) != "message.received":
        return {"status": "ignored"}
    message = event.get("message") or {}
    if not isinstance(message, dict):
        return {"status": "ignored"}

    sender = _parse_email_address(str(message.get("from", "")))
    if not sender:
        return {"status": "empty"}
    owner_email = settings.agentmail_owner_email.strip().lower()
    if owner_email and sender != owner_email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sender_not_allowed")

    subject = str(message.get("subject", "")).strip()
    text = str(message.get("text", "")).strip()
    if not text:
        return {"status": "empty"}
    # Give the model the subject as lightweight context.
    prompt = f"[Email · subject: {subject}]\n{text}" if subject else text
    message_id = str(message.get("message_id", "")) or None

    tenant_id, user_id = await ensure_owner(db)
    return await handle_inbound(
        db,
        channel=_EMAIL,
        installation=settings.agentmail_inbox_id or "inbox",
        notify=_email_notify,
        tenant_id=tenant_id,
        user_id=user_id,
        sender=sender,
        text=prompt,
        message_id=message_id,
    )


# --------------------------------------------------------------------------- #
# Owner-authed status + simulate + thread read (drive the Messaging page).      #
# --------------------------------------------------------------------------- #


class QQStatus(BaseModel):
    enabled: bool
    configured: bool
    app_id: str
    owner_openid_set: bool
    secret_set: bool
    webhook_path: str = "(WebSocket, no webhook)"


class EmailStatus(BaseModel):
    kind: str
    enabled: bool
    configured: bool
    inbox_id: str
    owner_email: str
    webhook_secret_set: bool
    webhook_path: str = "/channels/email/webhook"


class ThreadSummary(BaseModel):
    session_id: uuid.UUID
    channel: str
    external_id: str
    created_at: str


class ChannelsStatus(BaseModel):
    qq: QQStatus
    email: EmailStatus
    threads: list[ThreadSummary]


@router.get("/channels")
async def channels_status(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelsStatus:
    rows = (
        (
            await db.execute(
                select(SessionModel)
                .where(
                    SessionModel.tenant_id == ctx.tenant_id,
                    SessionModel.channel.in_((_CHANNEL, _EMAIL)),
                )
                .order_by(SessionModel.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    qq = await chan_svc.get_qq_config(db, ctx.tenant_id, ctx.user_id)
    return ChannelsStatus(
        qq=QQStatus(
            enabled=bool(qq and qq.enabled),
            configured=bool(qq and qq.enabled and qq.app_id and qq.secret_set),
            app_id=qq.app_id if qq else "",
            owner_openid_set=bool(qq and qq.owner_external_id),
            secret_set=bool(qq and qq.secret_set),
        ),
        email=EmailStatus(
            kind=settings.email_kind,
            enabled=settings.email_kind == "agentmail",
            configured=settings.email_kind == "agentmail" and bool(settings.agentmail_inbox_id),
            inbox_id=settings.agentmail_inbox_id,
            owner_email=settings.agentmail_owner_email,
            webhook_secret_set=bool(settings.agentmail_webhook_secret),
        ),
        threads=[
            ThreadSummary(
                session_id=r.id,
                channel=r.channel,
                external_id=r.external_scope_id,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
    )


class SimulateInbound(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=8192)]
    from_id: Annotated[str, Field(max_length=64)] = ""


class SimulateResult(BaseModel):
    status: str
    session_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    decision: str | None = None


@router.post("/channels/qq/simulate")
async def qq_simulate(
    body: SimulateInbound,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SimulateResult:
    """Inject an inbound QQ message as the owner (human-lane test without a bot)."""
    qq = await chan_svc.get_qq_config(db, ctx.tenant_id, ctx.user_id)
    sender = body.from_id or (qq.owner_external_id if qq else "") or "sim-owner"
    result = await handle_inbound(
        db,
        channel=_CHANNEL,
        installation=(qq.app_id if qq else "") or "local",
        notify=_qq_notify,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        sender=sender,
        text=body.text,
        message_id=None,
    )
    return SimulateResult(
        status=result["status"],
        session_id=uuid.UUID(result["session_id"]) if "session_id" in result else None,
        run_id=uuid.UUID(result["run_id"]) if "run_id" in result else None,
        decision=result.get("decision"),
    )


@router.post("/channels/email/simulate")
async def email_simulate(
    body: SimulateInbound,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SimulateResult:
    """Inject an inbound email as the owner (human-lane test without a live mailbox)."""
    sender = body.from_id or settings.agentmail_owner_email or "owner@example.com"
    result = await handle_inbound(
        db,
        channel=_EMAIL,
        installation=settings.agentmail_inbox_id or "inbox",
        notify=_email_notify,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        sender=sender,
        text=body.text,
        message_id=None,
    )
    return SimulateResult(
        status=result["status"],
        session_id=uuid.UUID(result["session_id"]) if "session_id" in result else None,
        run_id=uuid.UUID(result["run_id"]) if "run_id" in result else None,
        decision=result.get("decision"),
    )


class ThreadMessage(BaseModel):
    role: str
    text: str
    at: str


class PendingApprovalBrief(BaseModel):
    correlation_id: uuid.UUID
    short_id: str
    tool_name: str
    summary: str


class ThreadTranscript(BaseModel):
    session_id: uuid.UUID
    channel: str
    external_id: str
    messages: list[ThreadMessage]
    pending_approvals: list[PendingApprovalBrief]


@router.get("/channels/threads/{session_id}")
async def thread_transcript(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ThreadTranscript:
    sess = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if sess is None or sess.channel not in (_CHANNEL, _EMAIL):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread_not_found")
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.tenant_id == ctx.tenant_id,
                    Message.session_id == session_id,
                    Message.role.in_(("user", "assistant")),
                )
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    messages: list[ThreadMessage] = []
    for m in rows:
        content = await db.scalar(
            select(Part.content_redacted).where(
                Part.tenant_id == ctx.tenant_id,
                Part.message_id == m.id,
                Part.ordinal == 0,
            )
        )
        text = str((content or {}).get("text", "")).strip()
        if text:
            messages.append(ThreadMessage(role=m.role, text=text, at=m.created_at.isoformat()))

    pending = (
        (
            await db.execute(
                select(ApprovalEnvelope)
                .where(
                    ApprovalEnvelope.tenant_id == ctx.tenant_id,
                    ApprovalEnvelope.session_id == session_id,
                    ApprovalEnvelope.status == "pending",
                )
                .order_by(ApprovalEnvelope.requested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    approvals: list[PendingApprovalBrief] = []
    for env in pending:
        preview = env.preview_redacted or {}
        summary = str(preview.get("summary") or preview.get("title") or env.tool_name)
        approvals.append(
            PendingApprovalBrief(
                correlation_id=env.correlation_id,
                short_id=str(env.correlation_id).replace("-", "")[:8],
                tool_name=env.tool_name,
                summary=summary,
            )
        )
    return ThreadTranscript(
        session_id=session_id,
        channel=sess.channel,
        external_id=sess.external_scope_id,
        messages=messages,
        pending_approvals=approvals,
    )
