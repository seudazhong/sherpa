"""FastAPI entrypoint (walking-skeleton).

This is intentionally minimal: it boots and exposes health/readiness so a
coding agent has a green build to extend. Real routes are added per
docs/contracts/api.md and docs/IMPLEMENTATION.md.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.config import settings

app = FastAPI(title="Sherpa", version=__version__)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": settings.app_name, "version": __version__}


@app.get("/readyz")
async def readyz() -> dict[str, bool]:
    """Readiness probe. Extend to check DB/Redis once wired (IMPLEMENTATION.md)."""
    return {"ready": True}
