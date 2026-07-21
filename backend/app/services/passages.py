"""Archival/RAG passage memory service (milestone 1c).

User-private ``memory_passages`` with hybrid retrieval: a lexical branch
(Postgres FTS) and a vector branch (pgvector cosine), each tenant+user filtered
*before* ranking, fused with reciprocal-rank fusion (architect-review §pgvector).
Content is deduped by SHA-256. Embeddings come from ``app.memory`` (mockable
offline). The caller owns the transaction and commits.
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.memory import embed_one
from app.models import MemoryPassage
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound

_MAX_BYTES = 65536
_RRF_K = 60


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("archival memory requires a user context")
    return ctx.user_id


def _model_tag() -> str:
    return settings.embedding_model if settings.provider_kind != "mock" else "mock"


@dataclasses.dataclass(frozen=True)
class PassageHit:
    id: uuid.UUID
    text: str
    score: float


async def add_passage(
    db: AsyncSession, ctx: CallerContext, *, text: str, source: str = "agent"
) -> MemoryPassage:
    uid = _require_user(ctx)
    text = text.strip()
    if not text:
        raise Invalid("passage text is empty")
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise Invalid(f"passage exceeds {_MAX_BYTES} bytes")
    content_hash = hashlib.sha256(text.encode("utf-8")).digest()
    existing = await db.scalar(
        select(MemoryPassage).where(
            MemoryPassage.tenant_id == ctx.tenant_id,
            MemoryPassage.user_id == uid,
            MemoryPassage.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing
    embedding = await embed_one(text)
    row = MemoryPassage(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        text_content=text,
        embedding=embedding,
        embedding_model=_model_tag(),
        content_hash=content_hash,
        source=source,
    )
    db.add(row)
    await db.flush()
    return row


async def list_passages(
    db: AsyncSession, ctx: CallerContext, *, limit: int = 100
) -> list[MemoryPassage]:
    uid = _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(MemoryPassage)
                .where(MemoryPassage.tenant_id == ctx.tenant_id, MemoryPassage.user_id == uid)
                .order_by(MemoryPassage.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def delete_passage(db: AsyncSession, ctx: CallerContext, *, passage_id: uuid.UUID) -> None:
    uid = _require_user(ctx)
    row = await db.get(MemoryPassage, (ctx.tenant_id, passage_id))
    if row is None or row.user_id != uid:
        raise NotFound("passage not found")
    await db.delete(row)
    await db.flush()


async def search_passages(
    db: AsyncSession, ctx: CallerContext, *, query: str, k: int = 5
) -> list[PassageHit]:
    uid = _require_user(ctx)
    query = query.strip()
    if not query:
        return []

    # Vector branch — cosine distance, tenant+user filtered before ranking.
    qvec = await embed_one(query)
    vec_rows = (
        await db.execute(
            select(MemoryPassage.id, MemoryPassage.text_content)
            .where(MemoryPassage.tenant_id == ctx.tenant_id, MemoryPassage.user_id == uid)
            .order_by(MemoryPassage.embedding.cosine_distance(qvec))
            .limit(k)
        )
    ).all()

    # Lexical branch — Postgres FTS, same tenant+user filter.
    fts_rows = (
        await db.execute(
            sql_text(
                "SELECT id, text_content FROM memory_passages "
                "WHERE tenant_id = :t AND user_id = :u "
                "AND fts @@ plainto_tsquery('english', :q) "
                "ORDER BY ts_rank(fts, plainto_tsquery('english', :q)) DESC LIMIT :k"
            ),
            {"t": ctx.tenant_id, "u": uid, "q": query, "k": k},
        )
    ).all()

    # Reciprocal-rank fusion of the two ranked lists.
    fused: dict[uuid.UUID, tuple[float, str]] = {}
    for branch in (vec_rows, fts_rows):
        for rank, row in enumerate(branch):
            pid, ptext = row[0], row[1]
            prev = fused.get(pid, (0.0, ptext))
            fused[pid] = (prev[0] + 1.0 / (_RRF_K + rank + 1), ptext)
    hits = [PassageHit(id=pid, text=ptext, score=score) for pid, (score, ptext) in fused.items()]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
