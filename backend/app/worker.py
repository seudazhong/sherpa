"""arq worker: consumes run jobs and executes the core loop (docs/03, docs/04).

Run: `uv run arq app.worker.WorkerSettings`
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import select

from app.config import settings
from app.core import execute_run
from app.db import SessionLocal
from app.events import append_event, relay_once
from app.models import Run
from app.observability import bind_context, configure_logging, project_run_trace
from app.providers import build_provider
from app.redis_client import client as redis_client
from app.tools import build_default_registry


async def ping(ctx: dict[str, Any]) -> str:
    """Liveness job proving worker wiring."""
    return "pong"


async def _settle_failed(
    tenant_id: uuid.UUID, run_id: uuid.UUID, session_id: uuid.UUID | None
) -> None:
    """Settle a run as failed in a fresh transaction after a provider/loop error."""
    async with SessionLocal() as session:
        run = await session.get(Run, (tenant_id, run_id))
        if run is None or run.status in (
            "succeeded",
            "failed",
            "cancelled",
            "needs_reconciliation",
        ):
            return
        now = datetime.datetime.now(datetime.UTC)
        run.status = "failed"
        run.started_at = run.started_at or now
        run.settled_at = now
        await session.flush()
        await append_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=session_id,
            event_type="run.settled",
            payload={"reason": "failed", "status": "failed"},
        )
        await session.commit()


async def run_job(ctx: dict[str, Any], run_id: str) -> str:
    """Execute one durable run. v1 commits at settle; per-turn commit is a later refinement."""
    async with SessionLocal() as session:
        run = (
            await session.execute(select(Run).where(Run.id == uuid.UUID(run_id)))
        ).scalar_one_or_none()
        if run is None:
            return "unknown_run"
        tenant_id, rid, session_id = run.tenant_id, run.id, run.session_id
        bind_context(
            tenant_id=str(tenant_id),
            run_id=str(rid),
            session_id=str(session_id) if session_id is not None else None,
        )
        try:
            reason = await execute_run(
                session,
                run=run,
                provider=build_provider(),
                registry=build_default_registry(),
            )
            await project_run_trace(session, tenant_id=tenant_id, run_id=rid)
            await session.commit()
            return reason
        except Exception:
            await session.rollback()
            await _settle_failed(tenant_id, rid, session_id)
            return "failed"


async def _relay_loop() -> None:
    """Continuously publish outbox rows to Redis Streams (at-least-once delivery)."""
    while True:
        try:
            async with SessionLocal() as session:
                relayed = await relay_once(session, redis_client)
                await session.commit()
            await asyncio.sleep(0.2 if relayed else 0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1.0)


async def _startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    ctx["relay_task"] = asyncio.create_task(_relay_loop())


async def _shutdown(ctx: dict[str, Any]) -> None:
    task = ctx.get("relay_task")
    if task is not None:
        task.cancel()


class WorkerSettings:
    functions = [ping, run_job]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
