"""Session search projection: inline reindex + fused retrieval (ADR-029 P1).

The projection is a pure function of the canonical rows: ``reindex_session``
deletes and rebuilds every entry for one session from ``sessions`` (title),
``messages``/``parts`` (turns), and ``event_journal`` (tool calls). It runs
inline whenever a session changes (prompt admission, run settle), so it is
zero-lag and deterministically rebuildable. Retrieval fuses PostgreSQL ``simple``
FTS, an application-generated CJK bigram vector, and ``pg_trgm`` similarity.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventJournal, Message, Part, SessionSearchEntry
from app.models import Session as SessionModel
from app.services.context import CallerContext

_WS = re.compile(r"\s+")
_CJK_CLASS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_CJK = re.compile(f"[{_CJK_CLASS}]")
_CJK_RUN = re.compile(f"[{_CJK_CLASS}]+")
_MAX_CONTENT = 4000


def normalize(t: str) -> str:
    return _WS.sub(" ", t).strip().lower()


def cjk_bigrams(t: str) -> str:
    """Space-joined character bigrams of CJK runs (single chars kept alone)."""
    out: list[str] = []
    for run in _CJK_RUN.findall(t):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return " ".join(out)


def has_cjk(t: str) -> bool:
    return _CJK.search(t) is not None


def _entry(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    channel: str,
    source_kind: str,
    source_id: str,
    anchor_kind: str,
    anchor_id: str,
    content: str,
    occurred_at: datetime.datetime,
    run_id: uuid.UUID | None = None,
    message_seq: int | None = None,
    event_session_seq: int | None = None,
) -> SessionSearchEntry:
    content = content[:_MAX_CONTENT]
    return SessionSearchEntry(
        tenant_id=tenant_id,
        id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_id,
        source_kind=source_kind,
        source_id=source_id,
        anchor_kind=anchor_kind,
        anchor_id=anchor_id,
        run_id=run_id,
        message_seq=message_seq,
        event_session_seq=event_session_seq,
        channel=channel,
        content_text=content,
        normalized_text=normalize(content),
        cjk_terms=cjk_bigrams(content),
        occurred_at=occurred_at,
        projection_version=1,
    )


async def reindex_session(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    """Rebuild all search entries for one session from canonical rows. Idempotent."""
    sess = await session.get(SessionModel, (tenant_id, session_id))
    if sess is None:
        return
    await session.execute(
        delete(SessionSearchEntry).where(
            SessionSearchEntry.tenant_id == tenant_id,
            SessionSearchEntry.session_id == session_id,
        )
    )
    await session.flush()
    if sess.status == "deleted":
        return  # tombstoned: no searchable entries

    entries: list[SessionSearchEntry] = []
    now = datetime.datetime.now(datetime.UTC)

    if sess.title:
        entries.append(
            _entry(
                tenant_id=tenant_id,
                user_id=sess.user_id,
                session_id=session_id,
                channel=sess.channel,
                source_kind="title",
                source_id=str(session_id),
                anchor_kind="session",
                anchor_id=str(session_id),
                content=sess.title,
                occurred_at=sess.created_at or now,
            )
        )

    msgs = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.session_id == session_id,
                    Message.role.in_(("user", "assistant")),
                )
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    for m in msgs:
        content = await session.scalar(
            select(Part.content_redacted).where(
                Part.tenant_id == tenant_id, Part.message_id == m.id, Part.ordinal == 0
            )
        )
        txt = str((content or {}).get("text", "")).strip()
        if not txt:
            continue
        entries.append(
            _entry(
                tenant_id=tenant_id,
                user_id=sess.user_id,
                session_id=session_id,
                channel=sess.channel,
                source_kind="user_message" if m.role == "user" else "assistant_message",
                source_id=str(m.id),
                anchor_kind="message",
                anchor_id=str(m.id),
                content=txt,
                occurred_at=m.created_at or now,
                run_id=m.run_id,
                message_seq=m.seq,
            )
        )

    events = (
        (
            await session.execute(
                select(EventJournal)
                .where(
                    EventJournal.tenant_id == tenant_id,
                    EventJournal.session_id == session_id,
                    EventJournal.event_type == "tool-call",
                )
                .order_by(EventJournal.session_seq)
            )
        )
        .scalars()
        .all()
    )
    for ev in events:
        payload = ev.payload_redacted or {}
        name = str(payload.get("name", ""))
        args = str(payload.get("args", ""))
        tool_text = f"{name} {args}".strip()
        if not tool_text:
            continue
        entries.append(
            _entry(
                tenant_id=tenant_id,
                user_id=sess.user_id,
                session_id=session_id,
                channel=sess.channel,
                source_kind="tool",
                source_id=str(ev.id),
                anchor_kind="event",
                anchor_id=str(ev.id),
                content=tool_text,
                occurred_at=ev.created_at or now,
                run_id=ev.run_id,
                event_session_seq=ev.session_seq,
            )
        )

    session.add_all(entries)
    await session.flush()


async def reindex_all(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Full rebuild for a tenant (loops sessions). Returns session count."""
    ids = (
        (await session.execute(select(SessionModel.id).where(SessionModel.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    for sid in ids:
        await reindex_session(session, tenant_id, sid)
    return len(ids)


@dataclasses.dataclass(frozen=True)
class SearchHit:
    session_id: uuid.UUID
    kind: str
    anchor_kind: str
    anchor_id: str
    snippet: str
    additional_matches: int


_SEARCH_SQL = text("""
WITH params AS (
    SELECT
        websearch_to_tsquery('simple', :q) AS tsq,
        CAST(:has_cjk AS boolean) AS has_cjk,
        websearch_to_tsquery('simple', :cjk_q) AS cjkq,
        CAST(:norm AS text) AS norm
),
matched AS (
    SELECT
        e.session_id,
        e.source_kind,
        e.anchor_kind,
        e.anchor_id,
        e.content_text,
        (
            ts_rank_cd(e.fts, p.tsq) * (
                CASE e.source_kind
                    WHEN 'title' THEN 3.0
                    WHEN 'user_message' THEN 2.0
                    WHEN 'assistant_message' THEN 1.2
                    ELSE 1.0
                END
            )
            + CASE WHEN p.has_cjk AND e.cjk_fts @@ p.cjkq THEN 1.0 ELSE 0 END
            + CASE WHEN p.norm <> '' AND (p.norm <% e.normalized_text) THEN
                word_similarity(p.norm, e.normalized_text) * 0.5 ELSE 0 END
        ) AS score
    FROM session_search_entries e, params p
    WHERE e.tenant_id = :tid
      AND e.user_id = :uid
      AND e.redacted_at IS NULL
      AND (
          e.fts @@ p.tsq
          OR (p.has_cjk AND e.cjk_fts @@ p.cjkq)
          OR (p.norm <> '' AND (p.norm <% e.normalized_text))
      )
),
ranked AS (
    SELECT
        m.*,
        row_number() OVER (PARTITION BY m.session_id ORDER BY m.score DESC) AS rn,
        count(*) OVER (PARTITION BY m.session_id) AS matches
    FROM matched m
)
SELECT session_id, source_kind, anchor_kind, anchor_id, content_text, score, matches
FROM ranked
WHERE rn = 1 AND score > 0
ORDER BY score DESC
LIMIT :limit
""")


def _snippet(content: str, query: str, width: int = 140) -> str:
    """A bounded, plain-text excerpt centered on the first query token match."""
    norm = normalize(content)
    q = normalize(query)
    token = next((t for t in q.split() if t), "")
    idx = norm.find(token) if token else -1
    if idx < 0:
        return content[:width].strip()
    start = max(0, idx - width // 3)
    end = min(len(content), start + width)
    prefix = "\u2026" if start > 0 else ""
    suffix = "\u2026" if end < len(content) else ""
    return f"{prefix}{content[start:end].strip()}{suffix}"


async def search(
    session: AsyncSession,
    ctx: CallerContext,
    query: str,
    *,
    limit: int = 30,
) -> list[SearchHit]:
    q = query.strip()
    if not q:
        return []
    cjk = has_cjk(q)
    cjk_q = " OR ".join(cjk_bigrams(q).split()) if cjk else ""
    rows = (
        await session.execute(
            _SEARCH_SQL,
            {
                "q": q,
                "has_cjk": cjk,
                "cjk_q": cjk_q,
                "norm": normalize(q),
                "tid": ctx.tenant_id,
                "uid": ctx.user_id,
                "limit": limit,
            },
        )
    ).all()
    return [
        SearchHit(
            session_id=r.session_id,
            kind=r.source_kind,
            anchor_kind=r.anchor_kind,
            anchor_id=r.anchor_id,
            snippet=_snippet(r.content_text, q),
            additional_matches=int(r.matches) - 1,
        )
        for r in rows
    ]
