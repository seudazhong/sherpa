"""Knowledge source lifecycle service (ADR-036, KB1).

The capability layer for source-backed Knowledge: create a source from an owned Drive
file, list/get, reindex (bump generation + enqueue), remove (tombstone + cascade), and
mark sources stale when their backing file changes. Ingestion (snapshot → parse → chunk
→ embed → activate) is a durable worker job (KB2); this module only owns the source /
version / job *lifecycle* rows. The caller owns the transaction and commits.

Retrieval (KB3) and REST/Tool adapters (KB4) build on top; nothing here needs zhparser.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    DriveNode,
    EmbeddingProfile,
    KnowledgeIngestionJob,
    KnowledgeRetrievalEvidence,
    KnowledgeSource,
    KnowledgeSourceVersion,
)
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound

PARSER_VERSION = "v1"
PIPELINE_VERSION = "v1"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("knowledge requires a user context")
    return ctx.user_id


async def ensure_embedding_profile(db: AsyncSession, ctx: CallerContext) -> EmbeddingProfile:
    """Get-or-create the tenant's single active local embedding profile from config.

    A model/dim change means a NEW profile + full reindex (ADR-036); the profile row
    records the identity every indexed version pins.
    """
    name = "emb_local_v1"
    existing = await db.scalar(
        select(EmbeddingProfile).where(
            EmbeddingProfile.tenant_id == ctx.tenant_id,
            EmbeddingProfile.name == name,
            EmbeddingProfile.is_active.is_(True),
        )
    )
    if existing is not None:
        return existing
    profile = EmbeddingProfile(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        name=name,
        provider=settings.embedding_kind,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        normalize="cosine",
        privacy="external" if settings.embedding_kind == "openai_compatible" else "local",
    )
    db.add(profile)
    await db.flush()
    return profile


async def _require_owned_file(
    db: AsyncSession, ctx: CallerContext, file_id: uuid.UUID
) -> DriveNode:
    node = await db.get(DriveNode, (ctx.tenant_id, file_id))
    if node is None or node.user_id != _require_user(ctx):
        raise NotFound("file not found")
    if node.node_type != "file":
        raise Invalid("knowledge source must be a file, not a folder")
    if node.trashed_at is not None:
        raise Invalid("cannot index a trashed file")
    return node


async def _enqueue_version(
    db: AsyncSession, source: KnowledgeSource, node: DriveNode, profile: EmbeddingProfile
) -> tuple[KnowledgeSourceVersion, KnowledgeIngestionJob]:
    """Create the queued version + job for the source's current desired_generation."""
    gen = source.desired_generation
    idem = f"{source.id}:{gen}"
    version = KnowledgeSourceVersion(
        tenant_id=source.tenant_id,
        id=uuid.uuid4(),
        source_id=source.id,
        generation=gen,
        expected_file_version=node.version,
        expected_file_hash=node.content_hash,
        snapshot_object_key=f"knowledge/{source.id}/{gen}.snapshot",
        parser_version=PARSER_VERSION,
        pipeline_version=PIPELINE_VERSION,
        embedding_profile_id=profile.id,
        status="building",
        idempotency_key=idem,
    )
    job = KnowledgeIngestionJob(
        tenant_id=source.tenant_id,
        id=uuid.uuid4(),
        source_id=source.id,
        generation=gen,
        stage="queued",
        idempotency_key=idem,
    )
    db.add(version)
    db.add(job)
    await db.flush()
    return version, job


async def create_source(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    file_id: uuid.UUID,
    display_name: str | None = None,
) -> KnowledgeSource:
    """Add an owned Drive file as a knowledge source and enqueue its first index.

    Idempotent: re-adding a file that already has a live (non-tombstoned) source
    returns the existing source instead of creating a duplicate.
    """
    uid = _require_user(ctx)
    node = await _require_owned_file(db, ctx, file_id)

    existing = await db.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.tenant_id == ctx.tenant_id,
            KnowledgeSource.user_id == uid,
            KnowledgeSource.file_id == file_id,
            KnowledgeSource.tombstoned_at.is_(None),
        )
    )
    if existing is not None:
        return existing

    profile = await ensure_embedding_profile(db, ctx)
    source = KnowledgeSource(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        source_kind="file",
        file_id=file_id,
        display_name=(display_name or node.name).strip() or node.name,
        status="queued",
        desired_generation=1,
    )
    db.add(source)
    await db.flush()
    await _enqueue_version(db, source, node, profile)
    return source


async def list_sources(
    db: AsyncSession, ctx: CallerContext, *, limit: int = 100
) -> list[KnowledgeSource]:
    uid = _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(KnowledgeSource)
                .where(
                    KnowledgeSource.tenant_id == ctx.tenant_id,
                    KnowledgeSource.user_id == uid,
                    KnowledgeSource.tombstoned_at.is_(None),
                )
                .order_by(KnowledgeSource.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_source(
    db: AsyncSession, ctx: CallerContext, *, source_id: uuid.UUID
) -> KnowledgeSource:
    uid = _require_user(ctx)
    source = await db.get(KnowledgeSource, (ctx.tenant_id, source_id))
    if source is None or source.user_id != uid or source.tombstoned_at is not None:
        raise NotFound("knowledge source not found")
    return source


async def reindex_source(
    db: AsyncSession, ctx: CallerContext, *, source_id: uuid.UUID
) -> KnowledgeSource:
    """Bump desired_generation and enqueue a fresh version/job. The previous ready
    version stays active until the new one activates (never a half-built index)."""
    source = await get_source(db, ctx, source_id=source_id)
    if source.file_id is None:
        raise Invalid("source has no backing file")
    node = await _require_owned_file(db, ctx, source.file_id)
    profile = await ensure_embedding_profile(db, ctx)
    source.desired_generation += 1
    source.status = "queued"
    await db.flush()
    await _enqueue_version(db, source, node, profile)
    return source


async def remove_source(db: AsyncSession, ctx: CallerContext, *, source_id: uuid.UUID) -> None:
    """Tombstone + purge. Deleting the source cascades its versions/chunks/jobs; the
    immutable snapshots are swept by the GC worker (KB2). Never deletes the Drive file."""
    source = await get_source(db, ctx, source_id=source_id)
    await db.delete(source)
    await db.flush()


async def mark_stale_for_file(db: AsyncSession, ctx: CallerContext, *, file_id: uuid.UUID) -> int:
    """Mark ready sources of a changed file `stale`, bump their desired_generation, and
    auto-enqueue a reindex to the new generation (called on a Drive overwrite). The old
    active version stays searchable until the new one activates. Returns sources affected."""
    uid = _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(KnowledgeSource).where(
                    KnowledgeSource.tenant_id == ctx.tenant_id,
                    KnowledgeSource.user_id == uid,
                    KnowledgeSource.file_id == file_id,
                    KnowledgeSource.tombstoned_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    node = await _require_owned_file(db, ctx, file_id)
    profile = await ensure_embedding_profile(db, ctx)
    for source in rows:
        source.status = "stale"
        source.desired_generation += 1
        await db.flush()
        await _enqueue_version(db, source, node, profile)
    return len(rows)


async def tombstone_sources_for_files(
    db: AsyncSession, ctx: CallerContext, *, file_ids: Sequence[uuid.UUID]
) -> int:
    """Tombstone live sources of the given (deleted) Drive files — immediate retrieval
    exclusion. A maintenance sweep later hard-deletes the rows + snapshot objects.
    Returns the number of sources tombstoned."""
    if not file_ids:
        return 0
    uid = _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(KnowledgeSource).where(
                    KnowledgeSource.tenant_id == ctx.tenant_id,
                    KnowledgeSource.user_id == uid,
                    KnowledgeSource.file_id.in_(list(file_ids)),
                    KnowledgeSource.tombstoned_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for source in rows:
        source.tombstoned_at = _now()
        source.status = "deleting"
    await db.flush()
    return len(rows)


# --- maintenance / GC (system, no user context) -----------------------------


async def purge_expired_evidence(db: AsyncSession) -> int:
    """Delete retrieval-evidence rows past their retention TTL (ADR-036)."""
    result = await db.execute(
        delete(KnowledgeRetrievalEvidence).where(KnowledgeRetrievalEvidence.purge_after < _now())
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def gc_tombstoned_sources(db: AsyncSession) -> int:
    """Hard-delete tombstoned sources (cascades versions/chunks/jobs). Their now-orphan
    snapshot objects are removed by `sweep_orphan_snapshots`."""
    rows = (
        (
            await db.execute(
                select(KnowledgeSource).where(KnowledgeSource.tombstoned_at.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    for source in rows:
        await db.delete(source)
    await db.flush()
    return len(rows)


async def sweep_orphan_snapshots(db: AsyncSession) -> int:
    """Delete immutable snapshot objects with no owning version row (removed/superseded
    sources). Content-addressed keys live under the `knowledge/` prefix; deletion is
    never inline (mirrors the Drive blob GC, ADR-030)."""
    from app.objectstore import build_object_store

    live = set(
        (await db.execute(select(KnowledgeSourceVersion.snapshot_object_key))).scalars().all()
    )
    store = build_object_store()
    keys = await store.list_keys("knowledge/")
    deleted = 0
    for key in keys:
        if key not in live:
            await store.delete(key)
            deleted += 1
    return deleted
