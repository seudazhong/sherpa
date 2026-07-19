"""Health endpoint test — the walking-skeleton's green anchor."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_health() -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sherpa"
