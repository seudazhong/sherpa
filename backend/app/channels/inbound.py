"""Inbound IM message handling: map an external chat into the durable core loop.

An inbound IM message is turned into a Sherpa run exactly like a web prompt:

1. :func:`ensure_channel_session` find-or-creates a per-(channel, external id)
   session (``sessions.channel`` is the durable discriminator — the run itself
   keeps ``run_kind='web_chat'`` to stay inside the frozen ``runs`` CHECK).
2. :func:`admit_inbound` reuses :func:`app.core.admit_prompt` so the message is
   persisted before any model call (idempotent on the provider ``message_id`` via
   a uuid5, so a re-POSTed webhook never double-admits).
3. The worker executes the loop; :func:`compose_reply` + the outbound client push
   the final assistant text (and any pending approval preview) back to the user.

Approval on IM reuses the v1 approval base (ADR-020): a ``permission.asked`` gate
halts the action, we surface the preview + correlation id over IM, and an
``approve``/``reject`` reply (:func:`parse_command`) resolves the same envelope.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.qq import QQClient
from app.core import Admission, admit_prompt
from app.models import ApprovalEnvelope, Message, Part
from app.models import Session as SessionModel
from app.permissions import ResolveError, resolve_approval
from app.queue import enqueue_approval_resume, enqueue_run

Notifier = Callable[[str, str], Awaitable[None]]

_QQ_NS = uuid.uuid5(uuid.NAMESPACE_URL, "sherpa/channels/qq")

# IM "approve"/"reject" words → an approval choice (defaults to allow_once).
_APPROVE_WORDS = {"approve", "allow", "yes", "ok", "同意", "批准", "允许"}
_REJECT_WORDS = {"reject", "deny", "no", "cancel", "拒绝", "取消", "不"}


@dataclass(frozen=True)
class ApprovalCommand:
    """A parsed IM approval command: a choice + an optional correlation prefix."""

    choice: str
    correlation_prefix: str | None


def parse_command(text: str) -> ApprovalCommand | None:
    """Parse an IM approval command like ``approve 1a2b`` / ``reject`` / ``yes``.

    Returns ``None`` for anything that is not an approval verb, so normal chat
    falls through to the model.
    """
    parts = text.strip().lower().split()
    if not parts:
        return None
    verb = parts[0].strip(".,!:;")
    prefix = parts[1].strip(".,!:;") if len(parts) > 1 else None
    if verb in _APPROVE_WORDS:
        return ApprovalCommand(choice="allow_once", correlation_prefix=prefix)
    if verb in _REJECT_WORDS:
        return ApprovalCommand(choice="reject", correlation_prefix=prefix)
    return None


async def ensure_channel_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    installation_id: str,
    external_id: str,
) -> SessionModel:
    """Find-or-create the durable session for one external chat thread."""
    umo_key = f"{channel}:{installation_id}:{external_id}"
    existing = (
        await session.execute(
            select(SessionModel).where(
                SessionModel.tenant_id == tenant_id,
                SessionModel.umo_key == umo_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    sess = SessionModel(
        tenant_id=tenant_id,
        id=uuid.uuid4(),
        user_id=user_id,
        umo_key=umo_key,
        channel=channel,
        channel_installation_id=installation_id,
        scope_type="im",
        external_scope_id=external_id,
        status="open",
    )
    session.add(sess)
    await session.flush()
    return sess


async def admit_inbound(
    session: AsyncSession,
    *,
    sess: SessionModel,
    user_id: uuid.UUID,
    text: str,
    external_message_id: str | None,
) -> Admission:
    """Admit an inbound IM message as a durable prompt (idempotent per message id)."""
    if external_message_id:
        client_message_id = uuid.uuid5(_QQ_NS, f"{sess.umo_key}:{external_message_id}")
    else:
        client_message_id = uuid.uuid4()
    return await admit_prompt(
        session,
        tenant_id=sess.tenant_id,
        session_id=sess.id,
        user_id=user_id,
        client_message_id=client_message_id,
        text=text,
    )


async def final_assistant_text(
    session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> str | None:
    """Return the last assistant message text persisted for a run, if any."""
    message_id = await session.scalar(
        select(Message.id)
        .where(
            Message.tenant_id == tenant_id,
            Message.run_id == run_id,
            Message.role == "assistant",
        )
        .order_by(Message.seq.desc())
        .limit(1)
    )
    if message_id is None:
        return None
    content = await session.scalar(
        select(Part.content_redacted).where(
            Part.tenant_id == tenant_id,
            Part.message_id == message_id,
            Part.ordinal == 0,
        )
    )
    text = str((content or {}).get("text", "")).strip()
    return text or None


async def pending_approval_for_run(
    session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> ApprovalEnvelope | None:
    """Return a still-pending approval envelope raised by a run, if any."""
    return await session.scalar(
        select(ApprovalEnvelope)
        .where(
            ApprovalEnvelope.tenant_id == tenant_id,
            ApprovalEnvelope.run_id == run_id,
            ApprovalEnvelope.status == "pending",
        )
        .order_by(ApprovalEnvelope.requested_at.desc())
        .limit(1)
    )


async def find_pending_approval(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    correlation_prefix: str | None,
) -> ApprovalEnvelope | None:
    """Find the pending approval to resolve for an IM command.

    With a correlation prefix, match it; otherwise return the single most recent
    pending approval in the thread (the common "approve" with no id case).
    """
    rows = (
        (
            await session.execute(
                select(ApprovalEnvelope)
                .where(
                    ApprovalEnvelope.tenant_id == tenant_id,
                    ApprovalEnvelope.session_id == session_id,
                    ApprovalEnvelope.status == "pending",
                )
                .order_by(ApprovalEnvelope.requested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    if correlation_prefix:
        for row in rows:
            if (
                str(row.correlation_id)
                .replace("-", "")
                .startswith(correlation_prefix.replace("-", ""))
            ):
                return row
        return None
    return rows[0]


def _short(correlation_id: uuid.UUID) -> str:
    return str(correlation_id).replace("-", "")[:8]


def approval_preview_text(env: ApprovalEnvelope) -> str:
    """Render a pending approval as an IM prompt asking for approve/reject."""
    preview = env.preview_redacted or {}
    summary = preview.get("summary") or preview.get("title") or env.tool_name
    return (
        f"\n\n\u26a0\ufe0f Approval needed for {env.tool_name}: {summary}\n"
        f"Reply `approve {_short(env.correlation_id)}` or "
        f"`reject {_short(env.correlation_id)}`."
    )


def compose_reply(text: str | None, approval: ApprovalEnvelope | None) -> str:
    """Compose the outbound IM reply: assistant text + optional approval prompt."""
    body = text or "(no reply)"
    if approval is not None:
        body += approval_preview_text(approval)
    return body


async def deliver_run_reply(
    session: AsyncSession,
    *,
    client: QQClient,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    external_id: str,
) -> bool:
    """Push a settled run's reply (+ any pending approval prompt) to the IM user."""
    text = await final_assistant_text(session, tenant_id, run_id)
    approval = await pending_approval_for_run(session, tenant_id, run_id)
    if text is None and approval is None:
        return False
    body = compose_reply(text, approval)
    return await client.send_private(external_id, body)


# --------------------------------------------------------------------------- #
# Generic inbound routing — shared by the API (simulate/email) and the worker  #
# (QQ WebSocket). Turns one inbound message into an approval resolve or a run. #
# --------------------------------------------------------------------------- #


async def handle_inbound(
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
        return await resolve_over_channel(
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


async def resolve_over_channel(
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
