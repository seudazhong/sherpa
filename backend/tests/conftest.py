"""Shared test fixtures.

Data-plane isolation (ADR-044, backlog B-9) is installed by ``tests/__init__.py`` before
this module imports anything from ``app``; :func:`pytest_configure` below provisions the
dedicated database and re-proves the isolation actually took effect.

pytest-asyncio uses a fresh event loop per test; the module-global async engine
and Redis client pool connections, which must not be reused across loops. Reset
both after each test so the next test opens fresh connections on its own loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.config import settings
from app.db import engine
from app.redis_client import client as redis
from tests import db_guard


def pytest_configure(config: pytest.Config) -> None:
    """Prove the isolation, then create/adopt/migrate the dedicated test database.

    Safety is fail-closed, availability is best-effort: an unsafe target or a failed
    provisioning aborts the whole run, while an unreachable Postgres only warns — each
    integration test already skips itself via ``ping_db()``, which is how CI (and a
    laptop with the stack down) stays green.
    """
    if settings.owner_email != db_guard.TEST_OWNER_EMAIL:
        raise pytest.UsageError(
            "test isolation did not take effect: OWNER_EMAIL resolved to "
            f"{settings.owner_email!r} — app.config was imported before tests/__init__.py"
        )
    expected = db_guard.derive_test_database_url(settings.database_url)
    if db_guard.database_name(settings.database_url) != db_guard.database_name(expected):
        raise pytest.UsageError(
            "test isolation did not take effect: DATABASE_URL resolved to "
            f"{db_guard.database_name(settings.database_url)!r}"
        )

    try:
        outcome = asyncio.run(db_guard.provision_test_database(settings.database_url))
    except (TimeoutError, OSError) as exc:
        print(f"\n[sherpa] Postgres unreachable ({exc}); database-backed tests will skip.")
        return
    except db_guard.TestDatabaseGuardError as exc:
        raise pytest.UsageError(str(exc)) from exc
    print(f"\n[sherpa] test database {outcome} · redis {settings.redis_url}")


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    yield
    await engine.dispose()
    try:
        await redis.aclose()
    except Exception:
        pass
