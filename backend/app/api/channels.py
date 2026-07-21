"""IM / QQ channel REST (ADR-026, milestone 4).

Three surfaces:

- ``POST /channels/qq/webhook`` — inbound OneBot v11 events. Authenticated by an
  HMAC-SHA1 body signature (not a cookie) **and** an owner-id allowlist (single
  user, v1). A normal message is admitted as a durable run; an ``approve``/
  ``reject`` reply resolves the pending approval envelope over the ``qq`` channel
  (reusing the v1 approval base, ADR-020).
- ``GET /channels`` — owner-authed status projection + recent IM threads (drives
  the Messaging page + its "not configured" affordance).
- ``POST /channels/qq/simulate`` — owner-authed test hook that injects an inbound
  message as if from the owner's QQ id, so the human verification lane works
  without a live bot. ``GET /channels/threads/{id}`` reads a thread transcript.
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
    ApprovalCommand,
    admit_inbound,
    ensure_channel_session,
    find_pending_approval,
    parse_command,
)
from app.channels.qq import build_qq_client, verify_signature
from app.config import settings
from app.db import get_session
from app.models import ApprovalEnvelope, Message, Part
from app.models import Session as SessionModel
from app.permissions import ResolveError, resolve_approval
from app.queue import enqueue_approval_resume, enqueue_run

router = APIRouter(tags=["channels"])

_CHANNEL = "qq"


def _installation() -> str:
    return settings.qq_owner_id or "local"


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


async def _handle_inbound(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    sender: str,
    text: str,
    message_id: str | None,
) -> dict[str, str]:
    """Route one inbound IM message: approval command or a new run. Commits."""
    sess = await ensure_channel_session(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        channel=_CHANNEL,
        installation_id=_installation(),
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
            await build_qq_client().send_private(sender, "No pending approval to act on.")
            return {"status": "no_pending_approval"}
        return await _resolve_over_qq(db, env=env, user_id=user_id, sender=sender, command=command)

    admission = await admit_inbound(
        db, sess=sess, user_id=user_id, text=text, external_message_id=message_id
    )
    await db.commit()
    await enqueue_run(admission.run_id)
    return {"status": "queued", "run_id": str(admission.run_id), "session_id": str(sess.id)}


async def _resolve_over_qq(
    db: AsyncSession,
    *,
    env: ApprovalEnvelope,
    user_id: uuid.UUID,
    sender: str,
    command: ApprovalCommand,
) -> dict[str, str]:
    """Resolve a pending approval from an IM command (server-trusted verify)."""
    try:
        result = await resolve_approval(
            db,
            tenant_id=env.tenant_id,
            correlation_id=env.correlation_id,
            actor_id=user_id,
            channel=_CHANNEL,
            choice=command.choice,
            verify=lambda _row: None,  # server holds the envelope; owner is HMAC-authed
        )
    except ResolveError as exc:
        await db.rollback()
        await build_qq_client().send_private(sender, f"Could not resolve approval: {exc.detail}")
        return {"status": "resolve_failed"}

    await db.commit()
    if result.mutated:
        await enqueue_approval_resume(env.correlation_id)
    verb = "Rejected." if command.choice == "reject" else "Approved — running the action now."
    await build_qq_client().send_private(sender, verb)
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


class ThreadSummary(BaseModel):
    session_id: uuid.UUID
    external_id: str
    created_at: str


class ChannelsStatus(BaseModel):
    qq: QQStatus
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
                    SessionModel.channel == _CHANNEL,
                )
                .order_by(SessionModel.created_at.desc())
                .limit(20)
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
        threads=[
            ThreadSummary(
                session_id=r.id,
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


class ThreadTranscript(BaseModel):
    session_id: uuid.UUID
    external_id: str
    messages: list[ThreadMessage]


@router.get("/channels/threads/{session_id}")
async def thread_transcript(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ThreadTranscript:
    sess = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if sess is None or sess.channel != _CHANNEL:
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
    return ThreadTranscript(
        session_id=session_id, external_id=sess.external_scope_id, messages=messages
    )
