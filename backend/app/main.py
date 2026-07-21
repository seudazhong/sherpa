"""FastAPI entrypoint (walking-skeleton).

This is intentionally minimal: it boots and exposes health/readiness so a
coding agent has a green build to extend. Real routes are added per
docs/contracts/api.md and docs/IMPLEMENTATION.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status

from app import __version__
from app.api.activity import router as activity_router
from app.api.auth import router as auth_router
from app.api.candidates import router as candidates_router
from app.api.connectors import router as connectors_router
from app.api.notifications import router as notifications_router
from app.api.permissions import router as permissions_router
from app.api.prompt import router as prompt_router
from app.api.schedules import router as schedules_router
from app.api.sessions import router as sessions_router
from app.api.sse import router as sse_router
from app.api.todos import router as todos_router
from app.config import settings
from app.db import ping_db
from app.observability import configure_logging
from app.redis_client import ping_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield


app = FastAPI(title="Sherpa", version=__version__, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(connectors_router)
app.include_router(candidates_router)
app.include_router(todos_router)
app.include_router(schedules_router)
app.include_router(notifications_router)
app.include_router(permissions_router)
app.include_router(activity_router)
app.include_router(sse_router)
app.include_router(prompt_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": settings.app_name, "version": __version__}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    """Readiness probe: ready only when DB and Redis are both reachable."""
    checks = {"db": await ping_db(), "redis": await ping_redis()}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": checks}


@app.get("/meta")
async def meta() -> dict[str, object]:
    """Public client metadata: which provider/model currently backs the assistant."""
    real = settings.provider_kind != "mock"
    return {
        "version": __version__,
        "provider_kind": settings.provider_kind,
        "model": settings.provider_model if real else "mock",
        "real_model": real,
    }
