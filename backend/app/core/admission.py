"""Durable prompt admission (ADR-005, api.md §4, data-model.md DDL-note 4).

`admit_prompt` persists the user message + a `queued` run + advances
`sessions.admitted_seq` in the caller's transaction. The input row therefore
exists before any model call; a crash before the worker picks up the run leaves
it `queued` and re-enqueueable. Idempotency is keyed by `client_message_id`
(unique within tenant+session): a same-body retry returns the original
admission; a different-body reuse raises PromptConflict (HTTP 409).

Admission emits NO journal event: the event-type catalog is closed and has no
admission event, so the coordinator's `run.started` (emitted by the loop) is the
first session event. `event_cursor` is therefore the committed session-journal
tail at admission time; streaming `session_seq > cursor` yields `run.started`
onward without redefining the cursor format.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventJournal, Message, Part, Run
from app.models import Session as SessionModel

PROMPT_VERSION = "chat.v1"

_MARKDOWN = re.compile(r"[|#>*_`~\[\]()]+")
_WS = re.compile(r"\s+")


def _derive_title(text: str, limit: int = 80) -> str | None:
    """A clean, bounded title from the first user message (no markdown noise)."""
    cleaned = _WS.sub(" ", _MARKDOWN.sub(" ", text)).strip()
    if not cleaned:
        return None
    return cleaned[: limit - 1] + "\u2026" if len(cleaned) > limit else cleaned


class PromptConflict(Exception):
    """`client_message_id` reused with a different body (maps to HTTP 409)."""


@dataclasses.dataclass(frozen=True)
class Admission:
    session_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    admitted_seq: int
    state: str
    event_cursor: str
    reused: bool


async def _session_tail(session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID) -> int:
    val = await session.scalar(
        select(func.coalesce(func.max(EventJournal.session_seq), 0)).where(
            EventJournal.tenant_id == tenant_id, EventJournal.session_id == session_id
        )
    )
    return int(val or 0)


async def _next_seq(session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID) -> int:
    val = await session.scalar(
        select(func.coalesce(func.max(Message.seq), 0) + 1).where(
            Message.tenant_id == tenant_id, Message.session_id == session_id
        )
    )
    return int(val or 1)


async def _first_text(session: AsyncSession, tenant_id: uuid.UUID, message_id: uuid.UUID) -> str:
    content = await session.scalar(
        select(Part.content_redacted).where(
            Part.tenant_id == tenant_id, Part.message_id == message_id, Part.ordinal == 0
        )
    )
    return str((content or {}).get("text", ""))


async def admit_prompt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    client_message_id: uuid.UUID,
    text: str,
    run_kind: str = "web_chat",
) -> Admission:
    """Persist the prompt + a queued run. Caller owns the transaction (commit)."""
    existing = (
        await session.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.session_id == session_id,
                Message.client_message_id == client_message_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if await _first_text(session, tenant_id, existing.id) != text:
            raise PromptConflict(str(client_message_id))
        assert existing.run_id is not None
        cursor = await _session_tail(session, tenant_id, session_id)
        return Admission(
            session_id=session_id,
            message_id=existing.id,
            run_id=existing.run_id,
            admitted_seq=existing.seq,
            state="queued",
            event_cursor=str(cursor),
            reused=True,
        )

    seq = await _next_seq(session, tenant_id, session_id)
    run_id = uuid.uuid4()
    message_id = uuid.uuid4()

    session.add(
        Run(
            tenant_id=tenant_id,
            id=run_id,
            session_id=session_id,
            run_kind=run_kind,
            status="queued",
            admitted_seq=seq,
            prompt_version=PROMPT_VERSION,
        )
    )
    await session.flush()

    session.add(
        Message(
            tenant_id=tenant_id,
            id=message_id,
            session_id=session_id,
            run_id=run_id,
            author_user_id=user_id,
            client_message_id=client_message_id,
            seq=seq,
            role="user",
        )
    )
    await session.flush()

    session.add(
        Part(
            tenant_id=tenant_id,
            id=uuid.uuid4(),
            message_id=message_id,
            ordinal=0,
            kind="text",
            content_redacted={"text": text},
        )
    )
    await session.flush()

    sess = await session.get(SessionModel, (tenant_id, session_id))
    if sess is not None:
        sess.admitted_seq = seq
        sess.last_activity_at = datetime.datetime.now(datetime.UTC)
        # Derive a human title from the first user message when unset.
        if sess.title is None:
            sess.title = _derive_title(text)
        await session.flush()

    # Inline session-search projection (ADR-029 P1): the user message + title are
    # now searchable; the projection is a pure function of canonical rows.
    from app.search import reindex_session

    await reindex_session(session, tenant_id, session_id)

    cursor = await _session_tail(session, tenant_id, session_id)
    return Admission(
        session_id=session_id,
        message_id=message_id,
        run_id=run_id,
        admitted_seq=seq,
        state="queued",
        event_cursor=str(cursor),
        reused=False,
    )
