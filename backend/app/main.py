"""FastAPI entrypoint (walking-skeleton).

This is intentionally minimal: it boots and exposes health/readiness so a
coding agent has a green build to extend. Real routes are added per
docs/contracts/api.md and docs/IMPLEMENTATION.md.
"""

from __future__ import annotations

from fastapi import FastAPI, Response, status

from app import __version__
from app.api.auth import router as auth_router
from app.api.prompt import router as prompt_router
from app.api.sessions import router as sessions_router
from app.api.sse import router as sse_router
from app.config import settings
from app.db import ping_db
from app.redis_client import ping_redis

app = FastAPI(title="Sherpa", version=__version__)
app.include_router(auth_router)
app.include_router(sessions_router)
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
