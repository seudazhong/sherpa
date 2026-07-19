"""Readiness probe tests — DB/Redis pings are mocked (no live services)."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

import app.main as main
from app.main import app


async def _ok() -> bool:
    return True


async def _fail() -> bool:
    return False


async def _get_readyz() -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/readyz")


@pytest.mark.asyncio
async def test_readyz_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "ping_db", _ok)
    monkeypatch.setattr(main, "ping_redis", _ok)
    resp = await _get_readyz()
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"] == {"db": True, "redis": True}


@pytest.mark.asyncio
async def test_readyz_not_ready_when_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "ping_db", _fail)
    monkeypatch.setattr(main, "ping_redis", _ok)
    resp = await _get_readyz()
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["checks"]["db"] is False
