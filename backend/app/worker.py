"""arq worker: consumes run jobs and executes the core loop (docs/03, docs/04).

Run: `uv run arq app.worker.WorkerSettings`
"""

from __future__ import annotations

import uuid
from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import select

from app.config import settings
from app.core import execute_run
from app.db import SessionLocal
from app.models import Run
from app.observability import bind_context, configure_logging, project_run_trace
from app.providers import MockProvider
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


async def _startup(ctx: dict[str, Any]) -> None:
    configure_logging()


class WorkerSettings:
    functions = [ping, run_job]
    on_startup = _startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
