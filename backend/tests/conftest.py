"""Shared test fixtures.

pytest-asyncio uses a fresh event loop per test; the module-global async engine
pools connections, which must not be reused across loops. Dispose after each test
so the next test opens fresh connections on its own loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from app.db import engine


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    yield
    await engine.dispose()
