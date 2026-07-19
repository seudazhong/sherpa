"""Shared test fixtures.

pytest-asyncio uses a fresh event loop per test; the module-global async engine
and Redis client pool connections, which must not be reused across loops. Reset
both after each test so the next test opens fresh connections on its own loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from app.db import engine
from app.redis_client import client as redis


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    yield
    await engine.dispose()
    try:
        await redis.aclose()
    except Exception:
        pass
