"""arq worker (walking-skeleton).

The core agent loop runs here (docs/04-core-loop.md), consuming run jobs.
Kept minimal for now; functions are registered as implementation proceeds.
Run: `uv run arq app.worker.WorkerSettings`
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.config import settings


async def ping(ctx: dict[str, Any]) -> str:
    """Placeholder job proving the worker wiring; replaced by run-job handler."""
    return "pong"


class WorkerSettings:
    functions = [ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
