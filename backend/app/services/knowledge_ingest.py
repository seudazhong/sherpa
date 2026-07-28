"""Durable knowledge ingestion stage machine (ADR-036, KB2b).

Processes one ingestion job — a single source at a single generation — end to end,
bounded and re-entrant: **claim** (lease + generation fence) → **snapshot** (verify
the Drive file is unchanged, copy its exact bytes to the version's immutable object
key) → **parse** (no-tool) → **chunk** → **embed + fts** (bge-m3 vectors + best-effort
`sherpa_text` lexical) → **activate** (generation-fenced atomic switch of
`active_version_id`). Every exit has a named reason; a failure leaves the previous
`ready` version active. The durable job/version rows are the recovery source of truth
(ADR-016/017); re-running a job rebuilds its chunks idempotently.
"""

from __future__ import annotations

import datetime
import logging
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.knowledge import ParseError, chunk_document, parse_document
from app.memory import embed_texts
from app.models import (
    KnowledgeChunk,
    KnowledgeIngestionJob,
    KnowledgeSource,
    KnowledgeSourceVersion,
)
from app.services import drive as drive_svc
from app.services.context import CallerContext

logger = logging.getLogger("app.knowledge.ingest")

_LEASE_SECONDS = 600
_PROGRESS_TTL_SECONDS = 3600
_TS_CONFIG_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _progress_key(tenant_id: uuid.UUID, source_id: uuid.UUID, generation: int) -> str:
    return f"kb:progress:{tenant_id}:{source_id}:{generation}"


async def write_progress(
    tenant_id: uuid.UUID, source_id: uuid.UUID, generation: int, done: int, total: int
) -> None:
    """Publish embed progress to Redis. Deliberately NOT Postgres: the ingest
    transaction already holds a row lock on this job, so an autonomous write would
    deadlock. Progress is pure telemetry — Redis accelerates, it is never
    correctness-critical (ADR-016/017); losing it just falls back to the stage pill."""
    from app.redis_client import client as redis

    try:
        await redis.set(
            _progress_key(tenant_id, source_id, generation),
            f"{done}/{total}",
            ex=_PROGRESS_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - progress is best-effort
        logger.debug("knowledge ingest progress write skipped: %s", exc)


async def read_progress(
    tenant_id: uuid.UUID, source_id: uuid.UUID, generation: int
) -> tuple[int, int] | None:
    """Live `(done, total)` for an in-flight ingest, or None when unknown."""
    from app.redis_client import client as redis

    try:
        raw = await redis.get(_progress_key(tenant_id, source_id, generation))
    except Exception as exc:  # noqa: BLE001 - progress is best-effort
        logger.debug("knowledge ingest progress read skipped: %s", exc)
        return None
    if not raw:
        return None
    done, _, total = str(raw).partition("/")
    try:
        return int(done), int(total)
    except ValueError:
        return None


async def _clear_progress(tenant_id: uuid.UUID, source_id: uuid.UUID, generation: int) -> None:
    from app.redis_client import client as redis

    try:
        await redis.delete(_progress_key(tenant_id, source_id, generation))
    except Exception as exc:  # noqa: BLE001 - progress is best-effort
        logger.debug("knowledge ingest progress clear skipped: %s", exc)


def _ts_config() -> str:
    """The (trusted, deploy-config) text-search config name, validated as a bare
    identifier so it can be safely inlined (SQLAlchemy mis-parses `:param::regconfig`)."""
    cfg = settings.knowledge_text_search_config
    if not _TS_CONFIG_RE.match(cfg):
        raise ValueError(f"invalid text-search config: {cfg!r}")
    return cfg


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _load(
    db: AsyncSession, tenant_id: uuid.UUID, source_id: uuid.UUID, generation: int
) -> tuple[KnowledgeSource, KnowledgeSourceVersion, KnowledgeIngestionJob] | None:
    source = await db.get(KnowledgeSource, (tenant_id, source_id))
    if source is None:
        return None
    version = await db.scalar(
        select(KnowledgeSourceVersion).where(
            KnowledgeSourceVersion.tenant_id == tenant_id,
            KnowledgeSourceVersion.source_id == source_id,
            KnowledgeSourceVersion.generation == generation,
        )
    )
    job = await db.scalar(
        select(KnowledgeIngestionJob).where(
            KnowledgeIngestionJob.tenant_id == tenant_id,
            KnowledgeIngestionJob.source_id == source_id,
            KnowledgeIngestionJob.generation == generation,
        )
    )
    if version is None or job is None:
        return None
    return source, version, job


async def _fail(
    source: KnowledgeSource,
    version: KnowledgeSourceVersion,
    job: KnowledgeIngestionJob,
    *,
    code: str,
    fail_source: bool,
) -> str:
    version.status = "failed"
    version.failure_code = code
    job.stage = "failed"
    job.termination_reason = code
    if fail_source:
        source.status = "failed"
    await _clear_progress(source.tenant_id, source.id, job.generation)
    logger.warning(
        "knowledge ingest failed",
        extra={"source_id": str(source.id), "generation": job.generation, "reason": code},
    )
    return code


async def _populate_fts(db: AsyncSession, tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """Best-effort lexical index under the `sherpa_text` CJK config. Skipped (chunks
    keep a NULL `fts`) where zhparser is unavailable (e.g. CI) — the lexical branch is
    dormant there, consistent with migration 0027."""
    try:
        cfg = _ts_config()
        async with db.begin_nested():
            await db.execute(sql_text("SET LOCAL zhparser.multi_short = on"))
            await db.execute(
                sql_text(
                    f"UPDATE knowledge_chunks SET fts = to_tsvector('{cfg}', lexical_text) "
                    "WHERE tenant_id = :t AND version_id = :v"
                ),
                {"t": tenant_id, "v": version_id},
            )
    except Exception as exc:  # noqa: BLE001 - lexical is optional; vector still works
        logger.warning("knowledge fts skipped (sherpa_text unavailable): %s", exc)


async def process_ingestion(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    generation: int,
    lease_owner: str,
) -> str:
    """Run one ingestion job to a named terminal reason. Caller commits."""
    loaded = await _load(db, tenant_id, source_id, generation)
    if loaded is None:
        return "missing"
    source, version, job = loaded
    if job.stage in ("done", "failed"):
        return f"already_{job.stage}"

    # Claim + early generation fence.
    job.lease_owner = lease_owner
    job.lease_expires_at = _now() + datetime.timedelta(seconds=_LEASE_SECONDS)
    job.attempt += 1
    if source.tombstoned_at is not None or source.desired_generation != generation:
        version.status = "superseded"
        job.stage = "done"
        job.termination_reason = "superseded"
        return "superseded"
    if source.file_id is None:
        return await _fail(source, version, job, code="no_file", fail_source=True)

    ctx = CallerContext(tenant_id=tenant_id, user_id=source.user_id, actor="system")

    # Snapshot: verify the file is unchanged, copy its exact bytes to the immutable key.
    job.stage = "snapshot"
    try:
        node, data = await drive_svc.read_node(db, ctx, source.file_id)
    except Exception:  # noqa: BLE001 - a missing/unreadable file is a named exit
        return await _fail(source, version, job, code="file_unreadable", fail_source=True)
    if (
        node.version != version.expected_file_version
        or node.content_hash != version.expected_file_hash
    ):
        # The backing file changed since enqueue; a newer generation will index it.
        return await _fail(source, version, job, code="file_changed", fail_source=False)
    from app.files import build_object_store

    await build_object_store().put(version.snapshot_object_key, data, node.content_type)

    # Parse (no-tool) + chunk.
    job.stage = "parse"
    try:
        doc = parse_document(data, content_type=node.content_type, filename=node.name)
    except ParseError as exc:
        return await _fail(source, version, job, code=exc.code, fail_source=True)
    job.stage = "chunk"
    chunks = chunk_document(
        doc,
        target_tokens=settings.knowledge_chunk_target_tokens,
        overlap_tokens=settings.knowledge_chunk_overlap_tokens,
    )
    if not chunks:
        return await _fail(source, version, job, code="empty_document", fail_source=True)

    # Embed + write chunks (idempotent rebuild for this version).
    job.stage = "embed"
    await db.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.tenant_id == tenant_id, KnowledgeChunk.version_id == version.id
        )
    )
    total_chunks = len(chunks)
    await write_progress(tenant_id, source_id, generation, 0, total_chunks)

    async def on_progress(done: int, total: int) -> None:
        await write_progress(tenant_id, source_id, generation, done, total)

    try:
        embeddings = await embed_texts([c.text for c in chunks], progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - a dead/slow embedding backend is a named exit
        logger.warning(
            "knowledge embed failed",
            extra={"source_id": str(source_id), "generation": generation, "error": str(exc)},
        )
        return await _fail(source, version, job, code="embedding_failed", fail_source=True)

    for chunk, emb in zip(chunks, embeddings, strict=True):
        db.add(
            KnowledgeChunk(
                tenant_id=tenant_id,
                id=uuid.uuid4(),
                source_id=source_id,
                version_id=version.id,
                ordinal=chunk.ordinal,
                text_content=chunk.text,
                token_count=chunk.token_estimate,
                heading_path=chunk.heading_path,
                page=chunk.page,
                char_offset=chunk.char_offset,
                content_hash=chunk.content_hash,
                lexical_text=chunk.text,
                embedding=emb,
            )
        )
    await db.flush()
    await _populate_fts(db, tenant_id, version.id)
    version.chunk_count = len(chunks)
    version.language = doc.language
    version.status = "ready"

    # Activate — generation-fenced atomic switch (re-read the source in this txn).
    job.stage = "activate"
    fresh = await db.get(KnowledgeSource, (tenant_id, source_id))
    if fresh is None or fresh.tombstoned_at is not None or fresh.desired_generation != generation:
        version.status = "superseded"
        job.stage = "done"
        job.termination_reason = "superseded"
        await _clear_progress(tenant_id, source_id, generation)
        return "superseded"
    fresh.active_version_id = version.id
    fresh.status = "ready"
    version.activated_at = _now()
    job.stage = "done"
    job.termination_reason = "done"
    await _clear_progress(tenant_id, source_id, generation)
    logger.info(
        "knowledge ingest done",
        extra={
            "source_id": str(source_id),
            "generation": generation,
            "chunks": len(chunks),
            "language": doc.language,
        },
    )
    return "done"


async def recover_stuck_jobs(
    db: AsyncSession, *, limit: int = 50
) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Return (tenant_id, source_id, generation) for jobs to (re)dispatch: queued, or
    claimed with an expired lease. At-least-once recovery for the ingestion pipeline."""
    now = _now()
    rows = (
        (
            await db.execute(
                select(
                    KnowledgeIngestionJob.tenant_id,
                    KnowledgeIngestionJob.source_id,
                    KnowledgeIngestionJob.generation,
                )
                .where(
                    KnowledgeIngestionJob.stage.not_in(("done", "failed")),
                    (KnowledgeIngestionJob.lease_expires_at.is_(None))
                    | (KnowledgeIngestionJob.lease_expires_at < now),
                )
                .limit(limit)
            )
        )
        .tuples()
        .all()
    )
    return [(t, s, g) for t, s, g in rows]
