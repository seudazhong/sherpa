"""Hybrid knowledge retrieval with citations (ADR-036, KB3).

`search_knowledge` fuses a lexical branch (zhparser `sherpa_text` FTS — best-effort,
dormant where zhparser is absent) and a vector branch (pgvector cosine over bge-m3
embeddings) with reciprocal-rank fusion, after filtering by tenant/user/active-version
and excluding tombstoned sources **before ranking**. It returns bounded, structured
hits with stable `K:<tool_call_id>:N` citation references and persists the
provider-visible excerpts to the retention-scoped `knowledge_retrieval_evidence` table
(document text never enters the append-only journal, ADR-016/021). Nearest-neighbour
always returns rows, so a vector candidate only counts above a cosine-similarity floor;
`sufficient=False` (explicit "insufficient evidence") when nothing clears it.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.memory import embed_one
from app.models import KnowledgeChunk, KnowledgeRetrievalEvidence, KnowledgeSource
from app.services.context import CallerContext
from app.services.errors import Invalid

logger = logging.getLogger("app.knowledge.search")

_RRF_K = 60
_PER_SOURCE = 3
_EXCERPT = 400
_TS_CONFIG_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ts_config() -> str:
    cfg = settings.knowledge_text_search_config
    if not _TS_CONFIG_RE.match(cfg):
        raise ValueError(f"invalid text-search config: {cfg!r}")
    return cfg


@dataclasses.dataclass(frozen=True)
class KnowledgeHit:
    citation_ref: str
    source_id: uuid.UUID
    source_version_id: uuid.UUID
    chunk_id: uuid.UUID
    title: str
    page: int | None
    heading: str | None
    excerpt: str
    score: float
    matched_by: list[str]


@dataclasses.dataclass(frozen=True)
class KnowledgeSearchResult:
    query: str
    retrieval_invocation_id: uuid.UUID
    hits: list[KnowledgeHit]
    sufficient: bool


@dataclasses.dataclass(frozen=True)
class _Cand:
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    version_id: uuid.UUID
    page: int | None
    heading: str | None
    text: str
    title: str


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("knowledge search requires a user context")
    return ctx.user_id


def _cand(r: Any) -> _Cand:
    return _Cand(
        chunk_id=r.id,
        source_id=r.source_id,
        version_id=r.version_id,
        page=r.page,
        heading=r.heading_path,
        text=r.text_content,
        title=r.display_name,
    )


async def _vector_branch(
    db: AsyncSession, tenant_id: uuid.UUID, uid: uuid.UUID, qvec: list[float], limit: int
) -> list[_Cand]:
    """Nearest chunks of each source's active version, filtered before ranking. Only
    candidates at/above the cosine-similarity floor count (else NN always matches)."""
    dist = KnowledgeChunk.embedding.cosine_distance(qvec).label("dist")
    rows = (
        await db.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.source_id,
                KnowledgeChunk.version_id,
                KnowledgeChunk.page,
                KnowledgeChunk.heading_path,
                KnowledgeChunk.text_content,
                KnowledgeSource.display_name,
                dist,
            )
            .join(
                KnowledgeSource,
                (KnowledgeSource.tenant_id == KnowledgeChunk.tenant_id)
                & (KnowledgeSource.id == KnowledgeChunk.source_id),
            )
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeSource.user_id == uid,
                KnowledgeSource.tombstoned_at.is_(None),
                KnowledgeSource.active_version_id.is_not(None),
                KnowledgeChunk.version_id == KnowledgeSource.active_version_id,
            )
            .order_by(dist)
            .limit(limit)
        )
    ).all()
    floor = settings.knowledge_retrieval_min_score
    return [_cand(r) for r in rows if (1.0 - float(r.dist)) >= floor]


async def _lexical_branch(
    db: AsyncSession, tenant_id: uuid.UUID, uid: uuid.UUID, query: str, limit: int
) -> list[_Cand]:
    """Best-effort zhparser FTS (OR-fused terms for partial recall). Returns [] where
    the `sherpa_text` config is unavailable (lexical branch dormant)."""
    try:
        cfg = _ts_config()
        sql = sql_text(
            "WITH q AS ("
            f"  SELECT replace(plainto_tsquery('{cfg}', :query)::text, ' & ', ' | ')::tsquery tq"
            ") "
            "SELECT kc.id, kc.source_id, kc.version_id, kc.page, kc.heading_path, kc.text_content, "
            "       ks.display_name "
            "FROM knowledge_chunks kc "
            "JOIN knowledge_sources ks ON ks.tenant_id = kc.tenant_id AND ks.id = kc.source_id, q "
            "WHERE kc.tenant_id = :t AND ks.user_id = :u AND ks.tombstoned_at IS NULL "
            "  AND ks.active_version_id IS NOT NULL AND kc.version_id = ks.active_version_id "
            "  AND kc.fts @@ q.tq "
            "ORDER BY ts_rank(kc.fts, q.tq) DESC LIMIT :k"
        )
        async with db.begin_nested():
            await db.execute(sql_text("SET LOCAL zhparser.multi_short = on"))
            rows = (
                await db.execute(sql, {"query": query, "t": tenant_id, "u": uid, "k": limit})
            ).all()
    except Exception as exc:  # noqa: BLE001 - lexical is optional; vector still works
        logger.warning("knowledge lexical branch skipped (sherpa_text unavailable): %s", exc)
        return []
    return [_cand(r) for r in rows]


async def search_knowledge(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    query: str,
    k: int | None = None,
    tool_call_id: str | None = None,
    run_id: uuid.UUID | None = None,
    persist_evidence: bool = True,
) -> KnowledgeSearchResult:
    uid = _require_user(ctx)
    rid = uuid.uuid4()
    query = query.strip()
    if not query:
        return KnowledgeSearchResult(query, rid, [], False)
    k = k or settings.knowledge_retrieval_k
    candidate_n = max(k * 4, 20)
    qvec = await embed_one(query)

    vec = await _vector_branch(db, ctx.tenant_id, uid, qvec, candidate_n)
    lex = await _lexical_branch(db, ctx.tenant_id, uid, query, candidate_n)

    # Reciprocal-rank fusion; remember which branch(es) each chunk came from.
    scores: dict[uuid.UUID, float] = {}
    meta: dict[uuid.UUID, _Cand] = {}
    matched: dict[uuid.UUID, set[str]] = {}
    for branch, name in ((vec, "vector"), (lex, "lexical")):
        for rank, cand in enumerate(branch):
            scores[cand.chunk_id] = scores.get(cand.chunk_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            meta[cand.chunk_id] = cand
            matched.setdefault(cand.chunk_id, set()).add(name)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    hits: list[KnowledgeHit] = []
    per_source: dict[uuid.UUID, int] = {}
    for cid, score in ranked:
        cand = meta[cid]
        if per_source.get(cand.source_id, 0) >= _PER_SOURCE:
            continue
        per_source[cand.source_id] = per_source.get(cand.source_id, 0) + 1
        hits.append(
            KnowledgeHit(
                citation_ref=f"K:{tool_call_id or 'q'}:{len(hits) + 1}",
                source_id=cand.source_id,
                source_version_id=cand.version_id,
                chunk_id=cid,
                title=cand.title,
                page=cand.page,
                heading=cand.heading,
                excerpt=cand.text[:_EXCERPT],
                score=round(score, 6),
                matched_by=sorted(matched[cid]),
            )
        )
        if len(hits) >= k:
            break

    sufficient = bool(hits)
    if persist_evidence and hits:
        purge_after = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            days=settings.knowledge_evidence_retention_days
        )
        for hit in hits:
            db.add(
                KnowledgeRetrievalEvidence(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    user_id=uid,
                    retrieval_invocation_id=rid,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    citation_ref=hit.citation_ref,
                    source_id=hit.source_id,
                    source_version_id=hit.source_version_id,
                    chunk_id=hit.chunk_id,
                    excerpt=hit.excerpt,
                    score=hit.score,
                    matched_by=",".join(hit.matched_by),
                    purge_after=purge_after,
                )
            )
        await db.flush()

    logger.info(
        "knowledge searched",
        extra={
            "retrieval_invocation_id": str(rid),
            "candidates_vector": len(vec),
            "candidates_lexical": len(lex),
            "returned": len(hits),
            "sufficient": sufficient,
        },
    )
    return KnowledgeSearchResult(query, rid, hits, sufficient)
