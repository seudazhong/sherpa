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


async def enqueue_knowledge_ingest(
    tenant_id: uuid.UUID, source_id: uuid.UUID, generation: int
) -> None:
    """Enqueue a durable knowledge ingestion job (one source at one generation)."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job(
            "knowledge_ingest_job", str(tenant_id), str(source_id), int(generation)
        )
    finally:
        await pool.aclose()


async def enqueue_approval_resume(correlation_id: uuid.UUID) -> None:
    """Enqueue the resume job for a resolved approval (api.md §6.4 wake-up).

    Enqueued after the resolve commit (at-least-once, like enqueue_run); the resume
    job is idempotent on the bound invocation's settled state, so a redelivery is a
    no-op.
    """
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("approval_resume_job", str(correlation_id))
    finally:
        await pool.aclose()


async def enqueue_agent_task_dispatch() -> None:
    """Enqueue an immediate agent_task dispatch (Run Now; ADR-031 amendment).

    Dispatches due `agent_task` firings right away instead of waiting for the periodic
    `agent_task_tick`. Idempotent (firing slot + run_id guard), so racing the tick is a
    no-op.
    """
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("agent_task_dispatch_job")
    finally:
        await pool.aclose()


async def enqueue_project_import(tenant_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Enqueue a durable Project archive-import job (builds one project's initial
    immutable snapshot). At-least-once; a recovery tick re-dispatches stuck jobs."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("project_import_job", str(tenant_id), str(project_id))
    finally:
        await pool.aclose()


async def enqueue_project_runtime_open(tenant_id: uuid.UUID, runtime_session_id: uuid.UUID) -> None:
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("project_runtime_open_job", str(tenant_id), str(runtime_session_id))
    finally:
        await pool.aclose()


async def enqueue_project_runtime_exec(tenant_id: uuid.UUID, exec_run_id: uuid.UUID) -> None:
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("project_runtime_exec_job", str(tenant_id), str(exec_run_id))
    finally:
        await pool.aclose()


async def enqueue_project_runtime_close(
    tenant_id: uuid.UUID, runtime_session_id: uuid.UUID
) -> None:
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("project_runtime_close_job", str(tenant_id), str(runtime_session_id))
    finally:
        await pool.aclose()
