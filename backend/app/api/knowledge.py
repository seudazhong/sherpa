"""Knowledge base REST (ADR-036, KB4; ADR-023 parity with the *knowledge* tools).

Thin adapter over `app.services.knowledge` + `knowledge_search` so the Knowledge UI
(a `/library` page) and the agent tools share one capability layer. Reads need a
session; writes also need CSRF. Ingestion is enqueued best-effort after commit — the
worker's recovery tick guarantees at-least-once dispatch (ADR-016/017).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import KnowledgeIngestionJob, KnowledgeSource, KnowledgeSourceVersion
from app.services import CallerContext, ServiceError
from app.services import knowledge as svc
from app.services.knowledge_ingest import read_progress
from app.services.knowledge_search import search_knowledge

logger = logging.getLogger("app.api.knowledge")
router = APIRouter(tags=["knowledge"])

# Source statuses that mean "an ingest is in flight" (mirrors the UI's pills).
_IN_PROGRESS = ("queued", "parsing", "chunking", "embedding", "deleting")


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class KnowledgeSourceOut(BaseModel):
    id: uuid.UUID
    file_id: uuid.UUID | None
    display_name: str
    status: str
    stage: str | None
    progress_done: int | None
    progress_total: int | None
    active_version: int | None
    language: str | None
    chunk_count: int
    failure_code: str | None
    updated_at: datetime.datetime


class KnowledgeSourceCreate(BaseModel):
    file_id: uuid.UUID
    display_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class KnowledgeSourceBatchCreate(BaseModel):
    """Add several Drive files in one round-trip (each one is still idempotent)."""

    file_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=50)]


class KnowledgeBatchFailureOut(BaseModel):
    file_id: uuid.UUID
    code: str


class KnowledgeBatchResultOut(BaseModel):
    added: list[KnowledgeSourceOut]
    failed: list[KnowledgeBatchFailureOut]


class KnowledgeSearchQuery(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    k: Annotated[int, Field(ge=1, le=50)] = 6


class KnowledgeHitOut(BaseModel):
    citation_ref: str
    source_id: uuid.UUID
    source_version_id: uuid.UUID
    chunk_id: uuid.UUID
    title: str
    locator: dict[str, object]
    excerpt: str
    score: float
    matched_by: list[str]


class KnowledgeSearchResultOut(BaseModel):
    query: str
    retrieval_invocation_id: uuid.UUID
    hits: list[KnowledgeHitOut]
    sufficient: bool


async def _source_out(db: AsyncSession, source: KnowledgeSource) -> KnowledgeSourceOut:
    latest = await db.scalar(
        select(KnowledgeSourceVersion)
        .where(
            KnowledgeSourceVersion.tenant_id == source.tenant_id,
            KnowledgeSourceVersion.source_id == source.id,
        )
        .order_by(KnowledgeSourceVersion.generation.desc())
        .limit(1)
    )
    active = None
    if source.active_version_id is not None:
        active = await db.get(KnowledgeSourceVersion, (source.tenant_id, source.active_version_id))

    # Honest in-flight detail: the durable job stage plus (while embedding) live counts.
    stage: str | None = None
    done: int | None = None
    total: int | None = None
    if source.status in _IN_PROGRESS:
        job = await db.scalar(
            select(KnowledgeIngestionJob).where(
                KnowledgeIngestionJob.tenant_id == source.tenant_id,
                KnowledgeIngestionJob.source_id == source.id,
                KnowledgeIngestionJob.generation == source.desired_generation,
            )
        )
        stage = job.stage if job else None
        progress = await read_progress(source.tenant_id, source.id, source.desired_generation)
        if progress is not None:
            done, total = progress

    return KnowledgeSourceOut(
        id=source.id,
        file_id=source.file_id,
        display_name=source.display_name,
        status=source.status,
        stage=stage,
        progress_done=done,
        progress_total=total,
        active_version=active.generation if active else None,
        language=(active.language if active else (latest.language if latest else None)),
        chunk_count=active.chunk_count if active else 0,
        failure_code=latest.failure_code if latest else None,
        updated_at=source.updated_at,
    )


async def _enqueue(rc: RequestContext, source: KnowledgeSource) -> None:
    """Best-effort dispatch; the recovery tick re-dispatches queued jobs on a crash."""
    try:
        await queue.enqueue_knowledge_ingest(rc.tenant_id, source.id, source.desired_generation)
    except Exception as exc:  # noqa: BLE001 - the recovery tick is the safety net
        logger.warning("knowledge ingest enqueue skipped: %s", exc)


@router.get("/knowledge/sources")
async def list_sources(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[KnowledgeSourceOut]:
    rows = await svc.list_sources(db, _caller(ctx))
    return [await _source_out(db, r) for r in rows]


@router.post("/knowledge/sources", status_code=201)
async def add_source(
    body: KnowledgeSourceCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeSourceOut:
    try:
        source = await svc.create_source(
            db, _caller(ctx), file_id=body.file_id, display_name=body.display_name
        )
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    await _enqueue(ctx, source)
    return await _source_out(db, source)


@router.post("/knowledge/sources/batch", status_code=201)
async def add_sources_batch(
    body: KnowledgeSourceBatchCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBatchResultOut:
    """Add many Drive files at once. Each file is added idempotently and independently:
    one unreadable/unowned file yields a named failure instead of losing the batch."""
    created: list[KnowledgeSource] = []
    failed: list[KnowledgeBatchFailureOut] = []
    for file_id in dict.fromkeys(body.file_ids):
        try:
            created.append(await svc.create_source(db, _caller(ctx), file_id=file_id))
        except ServiceError as e:
            failed.append(KnowledgeBatchFailureOut(file_id=file_id, code=e.code))
    await db.commit()
    for source in created:
        await _enqueue(ctx, source)
    return KnowledgeBatchResultOut(added=[await _source_out(db, s) for s in created], failed=failed)


@router.get("/knowledge/sources/{source_id}")
async def get_source(
    source_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeSourceOut:
    try:
        source = await svc.get_source(db, _caller(ctx), source_id=source_id)
    except ServiceError as e:
        raise _http(e) from None
    return await _source_out(db, source)


@router.post("/knowledge/sources/{source_id}/reindex", status_code=202)
async def reindex_source(
    source_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeSourceOut:
    try:
        source = await svc.reindex_source(db, _caller(ctx), source_id=source_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    await _enqueue(ctx, source)
    return await _source_out(db, source)


@router.delete("/knowledge/sources/{source_id}", status_code=204)
async def remove_source(
    source_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    try:
        await svc.remove_source(db, _caller(ctx), source_id=source_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None


@router.post("/knowledge/search")
async def search(
    body: KnowledgeSearchQuery,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeSearchResultOut:
    try:
        result = await search_knowledge(db, _caller(ctx), query=body.query, k=body.k)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return KnowledgeSearchResultOut(
        query=result.query,
        retrieval_invocation_id=result.retrieval_invocation_id,
        hits=[
            KnowledgeHitOut(
                citation_ref=h.citation_ref,
                source_id=h.source_id,
                source_version_id=h.source_version_id,
                chunk_id=h.chunk_id,
                title=h.title,
                locator={"page": h.page, "heading": h.heading},
                excerpt=h.excerpt,
                score=h.score,
                matched_by=h.matched_by,
            )
            for h in result.hits
        ],
        sufficient=result.sufficient,
    )
