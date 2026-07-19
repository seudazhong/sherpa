"""arq worker: consumes run jobs and executes the core loop (docs/03, docs/04).

Run: `uv run arq app.worker.WorkerSettings`
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import select

from app.config import settings
from app.core import execute_run
from app.db import SessionLocal
from app.events import relay_once
from app.models import Run
from app.observability import bind_context, configure_logging, project_run_trace
from app.providers import MockProvider
from app.redis_client import client as redis_client
from app.tools import build_default_registry


async def ping(ctx: dict[str, Any]) -> str:
    """Liveness job proving worker wiring."""
    return "pong"


async def run_job(ctx: dict[str, Any], run_id: str) -> str:
    """Execute one durable run. v1 commits at settle; per-turn commit is a later refinement."""
    async with SessionLocal() as session:
        run = (
            await session.execute(select(Run).where(Run.id == uuid.UUID(run_id)))
        ).scalar_one_or_none()
        if run is None:
            return "unknown_run"
        bind_context(
            tenant_id=str(run.tenant_id),
            run_id=str(run.id),
            session_id=str(run.session_id) if run.session_id is not None else None,
        )
        reason = await execute_run(
            session,
            run=run,
            provider=MockProvider(),
            registry=build_default_registry(),
        )
        await project_run_trace(session, tenant_id=run.tenant_id, run_id=run.id)
        await session.commit()
        return reason


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
