"""arq enqueue helper for the run queue.

The web process durably admits a run (Postgres commit) and then enqueues the
run job. Enqueue is at-least-once; a crash between commit and enqueue leaves the
run `queued` for a recovery sweep to re-enqueue. A short-lived pool per call
keeps this correct across event loops (single-user v1); pooling is a later
refinement.
"""

from __future__ import annotations

import uuid

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings


async def enqueue_run(run_id: uuid.UUID) -> None:
    """Enqueue the durable run job onto the arq queue."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("run_job", str(run_id))
    finally:
        await pool.aclose()


async def enqueue_gmail_sync(connector_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Enqueue a Gmail sync job for a connector."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("gmail_sync_job", str(connector_id), str(run_id))
    finally:
        await pool.aclose()


async def enqueue_sync_and_analyze(connector_id: uuid.UUID) -> None:
    """Enqueue a combined sync+analyze job for a connector (periodic pipeline)."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("sync_and_analyze_job", str(connector_id))
    finally:
        await pool.aclose()
