"""IM + agentic-email inbound channels REST (ADR-026 QQ, ADR-027 email; milestones 4–5).

Per channel:

- ``POST /channels/{qq,email}/webhook`` — inbound events, authenticated by a body
  signature (QQ: OneBot HMAC-SHA1 + owner allowlist; email: Svix HMAC-SHA256) — not
  a cookie. A normal message is admitted as a durable run; an ``approve``/``reject``
  reply resolves the pending approval envelope over that channel (reusing the v1
  approval base, ADR-020). Both channels share one generic inbound path.
- ``GET /channels`` — owner-authed status projection + recent threads (drives the
  Messaging page + its "not configured" affordances).
- ``POST /channels/{qq,email}/simulate`` — owner-authed test hook injecting an
  inbound message, so the human verification lane works without a live bot/mailbox.
  ``GET /channels/threads/{id}`` reads a thread transcript (any channel).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, ensure_owner, require_context, require_csrf
from app.channels import (
    ApprovalCommand,
    admit_inbound,
    ensure_channel_session,
    find_pending_approval,
    parse_command,
)
from app.channels.email import build_email_channel_client, verify_svix_signature
from app.channels.qq import build_qq_client, verify_signature
from app.config import settings
from app.db import get_session
from app.models import ApprovalEnvelope, Message, Part
from app.models import Session as SessionModel
from app.permissions import ResolveError, resolve_approval
from app.queue import enqueue_approval_resume, enqueue_run

router = APIRouter(tags=["channels"])

_CHANNEL = "qq"
_EMAIL = "email"

Notifier = Callable[[str, str], Awaitable[None]]


async def _qq_notify(external_id: str, text: str) -> None:
    await build_qq_client().send_private(external_id, text)


async def _email_notify(external_id: str, text: str) -> None:
    await build_email_channel_client().send(to=external_id, subject="Sherpa", text=text)


# --------------------------------------------------------------------------- #
# Inbound webhook (HMAC + owner allowlist authenticated).                      #
# --------------------------------------------------------------------------- #


@router.post("/channels/qq/webhook")
async def qq_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if settings.qq_kind == "disabled":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "qq_disabled")
    raw = await request.body()
    if not verify_signature(settings.qq_webhook_secret, raw, request.headers.get("X-Signature")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")

    event = await request.json()
    if event.get("post_type") != "message" or event.get("message_type") != "private":
        return {"status": "ignored"}

    sender = str(event.get("user_id", ""))
    if not settings.qq_owner_id or sender != settings.qq_owner_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sender_not_allowed")

    text = _extract_text(event)
    if not text:
        return {"status": "empty"}
    message_id = str(event.get("message_id", "")) or None

    tenant_id, user_id = await ensure_owner(db)
    result = await _handle_inbound(
        db,
        channel=_CHANNEL,
        installation=settings.qq_owner_id or "local",
        notify=_qq_notify,
        tenant_id=tenant_id,
        user_id=user_id,
        sender=sender,
        text=text,
        message_id=message_id,
    )
    return result


def _extract_text(event: dict[str, object]) -> str:
    """Extract plain text from an OneBot message event (string or segment array)."""
    raw = event.get("raw_message")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    msg = event.get("message")
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, list):
        chunks = [
            str(seg.get("data", {}).get("text", ""))
            for seg in msg
            if isinstance(seg, dict) and seg.get("type") == "text"
        ]
        return "".join(chunks).strip()
    return ""


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
    return await _handle_inbound(
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


async def _handle_inbound(
    db: AsyncSession,
    *,
    channel: str,
    installation: str,
    notify: Notifier,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    sender: str,
    text: str,
    message_id: str | None,
) -> dict[str, str]:
    """Route one inbound message: approval command or a new run. Commits."""
    sess = await ensure_channel_session(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        channel=channel,
        installation_id=installation,
        external_id=sender,
    )

    command = parse_command(text)
    if command is not None:
        env = await find_pending_approval(
            db,
            tenant_id=tenant_id,
            session_id=sess.id,
            correlation_prefix=command.correlation_prefix,
        )
        if env is None:
            await db.commit()
            await notify(sender, "No pending approval to act on.")
            return {"status": "no_pending_approval"}
        return await _resolve_over_channel(
            db,
            env=env,
            user_id=user_id,
            sender=sender,
            command=command,
            channel=channel,
            notify=notify,
        )

    admission = await admit_inbound(
        db, sess=sess, user_id=user_id, text=text, external_message_id=message_id
    )
    await db.commit()
    await enqueue_run(admission.run_id)
    return {"status": "queued", "run_id": str(admission.run_id), "session_id": str(sess.id)}


async def _resolve_over_channel(
    db: AsyncSession,
    *,
    env: ApprovalEnvelope,
    user_id: uuid.UUID,
    sender: str,
    command: ApprovalCommand,
    channel: str,
    notify: Notifier,
) -> dict[str, str]:
    """Resolve a pending approval from an inbound command (server-trusted verify)."""
    try:
        result = await resolve_approval(
            db,
            tenant_id=env.tenant_id,
            correlation_id=env.correlation_id,
            actor_id=user_id,
            channel=channel,
            choice=command.choice,
            verify=lambda _row: None,  # server holds the envelope; sender is signature-authed
        )
    except ResolveError as exc:
        await db.rollback()
        await notify(sender, f"Could not resolve approval: {exc.detail}")
        return {"status": "resolve_failed"}

    await db.commit()
    if result.mutated:
        await enqueue_approval_resume(env.correlation_id)
    verb = "Rejected." if command.choice == "reject" else "Approved — running the action now."
    await notify(sender, verb)
    return {"status": "resolved", "decision": command.choice}


# --------------------------------------------------------------------------- #
# Owner-authed status + simulate + thread read (drive the Messaging page).      #
# --------------------------------------------------------------------------- #


class QQStatus(BaseModel):
    kind: str
    enabled: bool
    configured: bool
    owner_id_set: bool
    webhook_secret_set: bool
    api_base: str
    webhook_path: str = "/channels/qq/webhook"


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
    return ChannelsStatus(
        qq=QQStatus(
            kind=settings.qq_kind,
            enabled=settings.qq_kind != "disabled",
            configured=settings.qq_kind != "disabled" and bool(settings.qq_owner_id),
            owner_id_set=bool(settings.qq_owner_id),
            webhook_secret_set=bool(settings.qq_webhook_secret),
            api_base=settings.qq_api_base,
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
    """Inject an inbound IM message as the owner (human-lane test without a bot)."""
    sender = body.from_id or settings.qq_owner_id or "sim-owner"
    result = await _handle_inbound(
        db,
        channel=_CHANNEL,
        installation=settings.qq_owner_id or "local",
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
    result = await _handle_inbound(
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
