"""Session Library service (ADR-029 P0).

Browse + resume-state + recover + rename over the existing canonical session
spine (sessions/runs/messages/parts/approval_envelopes/effect_invocations). No
new canonical tables at P0; content search (session_search_entries) is P1.

Every query is scoped by ``tenant_id`` AND ``user_id`` (ADR-029): cross-user
reads are structurally impossible even when a tenant later has several users.
Resume-state is computed truthfully and never advertises an action that would
fail immediately (e.g. an expired-but-still-``pending`` approval).
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import uuid

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lease import run_is_live
from app.models import ApprovalEnvelope, EffectInvocation, EventJournal, Message, Part, Run
from app.models import Session as SessionModel
from app.search import SearchHit
from app.search import search as _search_index
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound

# Canonical run.status -> UI RunState projection (mirrors api/sessions._RUN_STATE).
_RUN_STATE = {
    "queued": "queued",
    "running": "running",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "interrupted",
    "needs_reconciliation": "needs_attention",
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclasses.dataclass(frozen=True)
class SessionView:
    session: SessionModel
    resume_state: str
    latest_run_state: str | None
    last_message_preview: str | None
    live: bool
    pending_approval_id: str | None
    unresolved_effect_id: str | None
    match: SearchHit | None = None


@dataclasses.dataclass(frozen=True)
class SessionBrowsePage:
    items: list[SessionView]
    next_cursor: str | None


def encode_cursor(activity: datetime.datetime | None, sid: uuid.UUID) -> str:
    stamp = activity.isoformat() if activity is not None else ""
    return base64.urlsafe_b64encode(f"{stamp}|{sid}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime.datetime | None, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, sid = raw.split("|", 1)
        activity = datetime.datetime.fromisoformat(stamp) if stamp else None
        return activity, uuid.UUID(sid)
    except Exception as exc:
        raise Invalid("bad_cursor") from exc


def _sort_key(s: SessionModel) -> datetime.datetime:
    return s.last_activity_at or s.created_at


async def _latest_run(db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID) -> Run | None:
    return (
        await db.execute(
            select(Run)
            .where(Run.tenant_id == tenant_id, Run.session_id == session_id)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _pending_approval(
    db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> ApprovalEnvelope | None:
    return (
        await db.execute(
            select(ApprovalEnvelope)
            .where(
                ApprovalEnvelope.tenant_id == tenant_id,
                ApprovalEnvelope.session_id == session_id,
                ApprovalEnvelope.status == "pending",
            )
            .order_by(ApprovalEnvelope.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _unresolved_effect(
    db: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> EffectInvocation | None:
    return (
        await db.execute(
            select(EffectInvocation)
            .where(
                EffectInvocation.tenant_id == tenant_id,
                EffectInvocation.run_id == run_id,
                EffectInvocation.outcome == "effect_unknown",
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _preview(db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID) -> str | None:
    mid = await db.scalar(
        select(Message.id)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
        .order_by(Message.seq.desc())
        .limit(1)
    )
    if mid is None:
        return None
    content = await db.scalar(
        select(Part.content_redacted).where(
            Part.tenant_id == tenant_id, Part.message_id == mid, Part.ordinal == 0
        )
    )
    if content is None:
        return None
    return str(content.get("text", ""))[:140]


def _resume_state(
    session: SessionModel,
    latest_run: Run | None,
    approval: ApprovalEnvelope | None,
    unresolved_effect: EffectInvocation | None,
    live: bool,
) -> str:
    if session.status == "archived":
        return "archived"
    if approval is not None:
        expires = approval.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=datetime.UTC)
        return "approval" if expires > _now() else "approval_expired"
    if unresolved_effect is not None:
        return "effect_unknown"
    if latest_run is None:
        return "ready"
    if latest_run.status == "needs_reconciliation":
        return "effect_unknown"
    if latest_run.status == "running":
        return "running" if live else "stale"
    if latest_run.status == "failed":
        return "failed"
    if latest_run.status == "cancelled":
        return "interrupted"
    # queued or succeeded
    return "ready"


async def _view(db: AsyncSession, session: SessionModel) -> SessionView:
    latest = await _latest_run(db, session.tenant_id, session.id)
    approval = await _pending_approval(db, session.tenant_id, session.id)
    effect = (
        await _unresolved_effect(db, session.tenant_id, latest.id) if latest is not None else None
    )
    live = run_is_live(
        latest.status if latest else None,
        latest.lease_expires_at if latest else None,
    )
    resume = _resume_state(session, latest, approval, effect, live)
    return SessionView(
        session=session,
        resume_state=resume,
        latest_run_state=_RUN_STATE.get(latest.status) if latest else None,
        last_message_preview=await _preview(db, session.tenant_id, session.id),
        live=live,
        pending_approval_id=str(approval.correlation_id) if approval else None,
        unresolved_effect_id=str(effect.invocation_id) if effect else None,
    )


async def browse(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    status: str | None = None,
    channel: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> SessionBrowsePage:
    """Recent sessions ordered by activity, keyset-paginated (tenant+user scoped)."""
    stmt = select(SessionModel).where(
        SessionModel.tenant_id == ctx.tenant_id,
        SessionModel.user_id == ctx.user_id,
        SessionModel.status != "deleted",
    )
    if channel is not None:
        stmt = stmt.where(SessionModel.channel == channel)
    activity = func.coalesce(SessionModel.last_activity_at, SessionModel.created_at)
    stmt = stmt.order_by(activity.desc(), SessionModel.id.desc()).limit(limit + 1)
    if cursor:
        cur_activity, cur_id = decode_cursor(cursor)
        anchor = cur_activity or datetime.datetime.min.replace(tzinfo=datetime.UTC)
        stmt = stmt.where(tuple_(activity, SessionModel.id) < (anchor, cur_id))

    rows = list((await db.execute(stmt)).scalars().all())
    next_cursor: str | None = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(_sort_key(last), last.id)
        rows = rows[:limit]

    views = [await _view(db, s) for s in rows]
    if status is not None:
        views = [v for v in views if v.resume_state == status]
    return SessionBrowsePage(items=views, next_cursor=next_cursor)


async def search_sessions(
    db: AsyncSession, ctx: CallerContext, query: str, *, limit: int = 30
) -> SessionBrowsePage:
    """Content search grouped by session (best match per session), tenant+user scoped."""
    hits = await _search_index(db, ctx, query, limit=limit)
    views: list[SessionView] = []
    for hit in hits:
        session = await db.get(SessionModel, (ctx.tenant_id, hit.session_id))
        if session is None or session.user_id != ctx.user_id or session.status == "deleted":
            continue
        base = await _view(db, session)
        views.append(dataclasses.replace(base, match=hit))
    return SessionBrowsePage(items=views, next_cursor=None)


async def _require_owned(
    db: AsyncSession, ctx: CallerContext, session_id: uuid.UUID
) -> SessionModel:
    session = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if session is None or session.user_id != ctx.user_id or session.status == "deleted":
        raise NotFound("session not found")
    return session


async def get_view(db: AsyncSession, ctx: CallerContext, session_id: uuid.UUID) -> SessionView:
    session = await _require_owned(db, ctx, session_id)
    return await _view(db, session)


async def rename(
    db: AsyncSession, ctx: CallerContext, session_id: uuid.UUID, title: str
) -> SessionView:
    session = await _require_owned(db, ctx, session_id)
    session.title = title
    await db.flush()
    return await _view(db, session)


async def timeline(
    db: AsyncSession,
    ctx: CallerContext,
    session_id: uuid.UUID,
    *,
    anchor_kind: str,
    anchor_id: str,
    before_turns: int = 20,
    after_turns: int = 20,
) -> tuple[list[Message], str]:
    """Messages around a typed anchor. Event/tool anchors resolve to their run's turn.

    Never compares ``messages.seq`` with ``event_journal.session_seq``: an event
    anchor is mapped to the message with the same ``run_id`` nearest the anchor.
    """
    session = await _require_owned(db, ctx, session_id)
    center_seq: int | None = None

    if anchor_kind == "message":
        try:
            mid = uuid.UUID(anchor_id)
        except ValueError as exc:
            raise Invalid("bad_anchor") from exc
        center_seq = await db.scalar(
            select(Message.seq).where(
                Message.tenant_id == ctx.tenant_id,
                Message.session_id == session.id,
                Message.id == mid,
            )
        )
    elif anchor_kind == "event":
        try:
            eid = uuid.UUID(anchor_id)
        except ValueError as exc:
            raise Invalid("bad_anchor") from exc
        run_id = await db.scalar(
            select(EventJournal.run_id).where(
                EventJournal.tenant_id == ctx.tenant_id,
                EventJournal.session_id == session.id,
                EventJournal.id == eid,
            )
        )
        if run_id is not None:
            center_seq = await db.scalar(
                select(func.max(Message.seq)).where(
                    Message.tenant_id == ctx.tenant_id,
                    Message.session_id == session.id,
                    Message.run_id == run_id,
                )
            )

    if center_seq is None:
        center_seq = await db.scalar(
            select(func.max(Message.seq)).where(
                Message.tenant_id == ctx.tenant_id, Message.session_id == session.id
            )
        )
    center_seq = int(center_seq or 0)
    low, high = center_seq - before_turns, center_seq + after_turns

    msgs = list(
        (
            await db.execute(
                select(Message)
                .where(
                    Message.tenant_id == ctx.tenant_id,
                    Message.session_id == session.id,
                    Message.role.in_(("user", "assistant")),
                    Message.seq >= low,
                    Message.seq <= high,
                )
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    return msgs, str(center_seq)


async def recover(
    db: AsyncSession, ctx: CallerContext, session_id: uuid.UUID, action: str
) -> SessionView:
    """State-specific reconciliation (not a generic retry).

    - ``recheck``: re-read state (a stale lease may have refreshed / settled).
    - ``verified``: the human confirmed an unknown external effect happened;
      resolve the effect + settle the run instead of blind-retrying (ADR-017).
    - ``new_run``: mark a stale/interrupted run cancelled so a fresh prompt starts
      a new run; never reuses an effect_unknown outcome.
    """
    session = await _require_owned(db, ctx, session_id)
    latest = await _latest_run(db, ctx.tenant_id, session.id)

    if action == "verified":
        effect = (
            await _unresolved_effect(db, ctx.tenant_id, latest.id) if latest is not None else None
        )
        if effect is not None:
            effect.status = "settled"
            effect.outcome = "succeeded"
            effect.reconciliation_state = "resolved_succeeded"
            effect.settled_at = effect.settled_at or _now()
            await db.flush()
        if latest is not None and latest.status == "needs_reconciliation":
            latest.status = "succeeded"
            latest.settled_at = latest.settled_at or _now()
            await db.flush()
    elif action == "new_run":
        if latest is not None and latest.status in ("running", "queued"):
            latest.status = "cancelled"
            latest.settled_at = _now()
            latest.lease_expires_at = None
            await db.flush()
    elif action != "recheck":
        raise Invalid("bad_action")

    return await _view(db, session)
