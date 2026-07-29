"""Unit tests for the test-suite isolation guard (ADR-044, backlog B-9).

Deliberately non-destructive: nothing here deletes a row or provisions a database. The
fail-closed paths are proven with a fake session (no SQL may reach any server) and, for
the environment shim, with a throwaway subprocess.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.config import settings
from app.models import Base
from tests import db_guard

BACKEND_DIR = Path(__file__).resolve().parent.parent


# --- URL derivation ---------------------------------------------------------


def test_derive_appends_test_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    derived = db_guard.derive_test_database_url(
        "postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa"
    )
    assert derived == "postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa_test"
    assert db_guard.database_name(derived) == "sherpa_test"


def test_derive_is_idempotent_for_an_already_test_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    url = "postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa_test"
    assert db_guard.derive_test_database_url(url) == url


def test_explicit_test_database_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/other")
    assert (
        db_guard.derive_test_database_url(
            "postgresql+asyncpg://sherpa:sherpa@localhost:5432/sherpa"
        )
        == "postgresql+asyncpg://u:p@host:5432/other"
    )


def test_redis_is_isolated_to_a_dedicated_logical_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_REDIS_URL", raising=False)
    assert db_guard.derive_test_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/15"


def test_explicit_test_redis_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_REDIS_URL", "redis://localhost:6379/9")
    assert db_guard.derive_test_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/9"


def test_maintenance_url_targets_the_postgres_database() -> None:
    admin = db_guard.maintenance_url("postgresql+asyncpg://u:p@h:5432/sherpa_test")
    assert db_guard.database_name(admin) == "postgres"


# --- the environment shim actually took effect ------------------------------


def test_this_process_runs_against_the_isolated_infrastructure() -> None:
    """The live settings singleton — not just the derivation helpers — must be isolated."""
    assert db_guard.database_name(settings.database_url).endswith("_test")
    assert settings.redis_url.rsplit("/", 1)[-1] != "0"
    assert settings.owner_email == db_guard.TEST_OWNER_EMAIL
    assert settings.owner_email != "owner@localhost"


def test_the_suite_owner_is_synthetic() -> None:
    """`owner_ids()` derives from OWNER_EMAIL, so the deleted tenant is never the real one."""
    from app.auth import owner_ids

    assert owner_ids()[0] == uuid.uuid5(
        uuid.NAMESPACE_URL, f"sherpa:tenant:{db_guard.TEST_OWNER_EMAIL}"
    )
    assert owner_ids()[0] != uuid.uuid5(uuid.NAMESPACE_URL, "sherpa:tenant:owner@localhost")


def test_pointing_the_harness_at_the_app_database_fails_closed() -> None:
    """A same-database TEST_DATABASE_URL must abort at import — before any connection."""
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/appdb",
        "TEST_DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/appdb",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import tests"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "TestDatabaseGuardError" in proc.stderr
    assert "application database" in proc.stderr


# --- fail-closed destructive gate -------------------------------------------


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeSession:
    """Records every statement so a test can assert that NO SQL was emitted."""

    def __init__(self, statements: list[str], marker: Any) -> None:
        self._statements = statements
        self._marker = marker

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        self._statements.append(str(statement))
        return _FakeResult(self._marker)

    async def commit(self) -> None:
        return None


def _fake_session_factory(statements: list[str], marker: Any) -> Any:
    def factory() -> _FakeSession:
        return _FakeSession(statements, marker)

    return factory


@pytest.fixture(autouse=True)
def _forget_verification() -> Any:
    """The guard caches a positive verification per URL; isolate each test from it."""
    db_guard.reset_verification_cache()
    yield
    db_guard.reset_verification_cache()


async def test_assert_test_database_rejects_a_database_without_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db as app_db

    statements: list[str] = []
    monkeypatch.setattr(app_db, "SessionLocal", _fake_session_factory(statements, None))

    with pytest.raises(db_guard.TestDatabaseGuardError) as excinfo:
        await db_guard.assert_test_database()
    assert db_guard.MARKER_TABLE in str(excinfo.value)
    assert all("DELETE" not in stmt.upper() for stmt in statements)


async def test_assert_test_database_accepts_a_marked_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db as app_db

    statements: list[str] = []
    monkeypatch.setattr(
        app_db, "SessionLocal", _fake_session_factory(statements, db_guard.MARKER_TABLE)
    )
    await db_guard.assert_test_database()
    assert any("to_regclass" in stmt for stmt in statements)


async def test_drop_tenant_emits_no_sql_when_the_guard_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of B-9: an unverified database must not receive the DELETE."""
    import app.db as app_db

    statements: list[str] = []
    monkeypatch.setattr(app_db, "SessionLocal", _fake_session_factory(statements, None))

    with pytest.raises(db_guard.TestDatabaseGuardError):
        await db_guard.drop_tenant(uuid.uuid4())
    assert all("DELETE" not in stmt.upper() for stmt in statements)


async def test_drop_tenant_bounds_locks_on_a_verified_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded DELETE (lock_timeout) is what keeps a live worker from deadlocking us."""
    import app.db as app_db

    statements: list[str] = []
    monkeypatch.setattr(
        app_db, "SessionLocal", _fake_session_factory(statements, db_guard.MARKER_TABLE)
    )
    await db_guard.drop_tenant(uuid.uuid4())
    assert any("lock_timeout" in stmt for stmt in statements)
    assert any("DELETE FROM tenants" in stmt for stmt in statements)


# --- schema hygiene ---------------------------------------------------------


def test_marker_table_is_not_part_of_the_application_schema() -> None:
    """It must stay invisible to alembic autogenerate (ADR-044)."""
    assert db_guard.MARKER_TABLE not in Base.metadata.tables
