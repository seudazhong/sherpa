"""Test-suite data-plane isolation + fail-closed destructive guard (ADR-044, backlog B-9).

The suite used to share ONE Postgres/Redis with the running dev stack and clean up by
deleting the *configured* owner tenant, which cascaded away the developer's real data
(model sources, projects, sessions) and deadlocked against the worker's cron. The fix is
process-level isolation applied in four layers:

L0  :func:`apply_test_environment` rewrites ``DATABASE_URL`` / ``REDIS_URL`` /
    ``OWNER_EMAIL`` (plus the on-disk scratch roots) **before** ``app.config`` is ever
    imported. It runs from ``tests/__init__.py``, the first module Python executes for
    the ``tests`` package, so the ``Settings`` singleton is born already isolated.
L1  :func:`provision_test_database` creates the dedicated database and stamps the marker
    table (see :data:`MARKER_TABLE`).
L2  :func:`assert_test_database` is the fail-closed gate: the marker table is the ONLY
    evidence accepted that a database may be written destructively.
L3  :func:`drop_tenant` is the single entry point every destructive cleanup uses.

This module must not import ``app.*`` at module level — that would build ``Settings``
before L0 has run. App imports are deliberately function-local.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Marker table proving a database was provisioned BY THIS HARNESS for throwaway data.
# It is intentionally absent from `Base.metadata`: never autogenerate a migration
# against the test database, or alembic will propose dropping it (ADR-044).
MARKER_TABLE = "_sherpa_test_marker"

# The synthetic owner. `app.auth.owner.owner_ids()` derives the tenant/user uuid5 from
# this address, so even if every other layer failed, the tenant the suite deletes is
# not the one the dev stack logs in as.
TEST_OWNER_EMAIL = "test-owner@sherpa.test"

# Redis logical database reserved for tests (the stack uses /0), so the dev worker never
# consumes a job the suite enqueued and leader locks do not collide.
TEST_REDIS_DB = 15

_BACKEND_DIR = Path(__file__).resolve().parent.parent

_env_applied = False
_verified_url: str | None = None


class TestDatabaseGuardError(RuntimeError):
    """Raised when the suite cannot prove it is pointed at a throwaway database."""


# --- L0: environment isolation ----------------------------------------------


def derive_test_database_url(app_url: str) -> str:
    """Return the dedicated test database URL for ``app_url``.

    ``TEST_DATABASE_URL`` wins when set; otherwise the application database name gets a
    ``_test`` suffix (``…/sherpa`` → ``…/sherpa_test``). An app URL that already ends in
    ``_test`` is returned unchanged rather than growing a second suffix.
    """
    override = os.environ.get("TEST_DATABASE_URL", "").strip()
    if override:
        return override
    parts = urlsplit(app_url)
    name = parts.path.lstrip("/")
    if not name:
        raise TestDatabaseGuardError(f"DATABASE_URL has no database name: {app_url!r}")
    if not name.endswith("_test"):
        name = f"{name}_test"
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def derive_test_redis_url(app_url: str) -> str:
    """Return the isolated Redis URL (logical db 15). ``TEST_REDIS_URL`` wins when set."""
    override = os.environ.get("TEST_REDIS_URL", "").strip()
    if override:
        return override
    parts = urlsplit(app_url)
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/{TEST_REDIS_DB}", parts.query, parts.fragment)
    )


def maintenance_url(test_url: str) -> str:
    """Return ``test_url`` retargeted at the ``postgres`` maintenance database."""
    parts = urlsplit(test_url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def apply_test_environment() -> None:
    """Point this process at throwaway infrastructure. Idempotent; call before app import."""
    global _env_applied
    if _env_applied:
        return

    import app.config as app_config

    # Resolve what the APPLICATION would have used (env > .env > field defaults), so the
    # derivation matches the developer's real stack rather than a guessed default.
    probe = app_config.Settings()
    app_db_url, app_redis_url = probe.database_url, probe.redis_url

    test_db_url = derive_test_database_url(app_db_url)
    if database_name(test_db_url) == database_name(app_db_url):
        raise TestDatabaseGuardError(
            "refusing to run: the resolved test database is the application database "
            f"({database_name(app_db_url)!r}). Unset or correct TEST_DATABASE_URL."
        )

    scratch = Path(tempfile.gettempdir()) / "sherpa-tests"
    os.environ["DATABASE_URL"] = test_db_url
    os.environ["REDIS_URL"] = derive_test_redis_url(app_redis_url)
    os.environ["OWNER_EMAIL"] = TEST_OWNER_EMAIL
    os.environ["TOOL_OUTPUT_ROOT"] = str(scratch / "tool-output")
    os.environ["SANDBOX_SCRATCH_ROOT"] = str(scratch / "scratch")

    # Importing app.config above already built its module-level singleton from the OLD
    # environment; rebuild it so every later `from app.config import settings` — and the
    # engine app.db derives from it — sees the isolated values.
    app_config.settings = app_config.Settings()
    _env_applied = True


# --- L1: provisioning -------------------------------------------------------


async def _connect(url: str):  # type: ignore[no-untyped-def]
    import asyncpg

    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def _database_exists(admin_url: str, name: str) -> bool:
    conn = await _connect(admin_url)
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name))
    finally:
        await conn.close()


async def _create_database(admin_url: str, name: str) -> None:
    conn = await _connect(admin_url)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _drop_database(admin_url: str, name: str) -> None:
    conn = await _connect(admin_url)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


async def _has_marker(url: str) -> bool:
    conn = await _connect(url)
    try:
        return await conn.fetchval("SELECT to_regclass($1)", f"public.{MARKER_TABLE}") is not None
    finally:
        await conn.close()


async def _write_marker(url: str, *, note: str) -> None:
    conn = await _connect(url)
    try:
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {MARKER_TABLE} ("
            "  id integer PRIMARY KEY,"
            "  created_at timestamptz NOT NULL DEFAULT now(),"
            "  note text NOT NULL)"
        )
        await conn.execute(
            f"INSERT INTO {MARKER_TABLE} (id, note) VALUES (1, $1) ON CONFLICT (id) DO NOTHING",
            note,
        )
    finally:
        await conn.close()


def _run_migrations(url: str) -> None:
    """`alembic upgrade head` in a subprocess (its own asyncio.run would clash with pytest)."""
    env = {**os.environ, "DATABASE_URL": url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise TestDatabaseGuardError(
            "alembic upgrade head failed against the test database "
            f"{database_name(url)!r}:\n{proc.stdout}\n{proc.stderr}"
        )


async def provision_test_database(url: str) -> str:
    """Create/adopt/reset the dedicated test database and stamp the marker.

    Returns a short outcome string for the session banner. Raises
    :class:`TestDatabaseGuardError` for anything unsafe — never degrades to the app
    database. Connectivity failures are the caller's to interpret (tests skip
    themselves when Postgres is absent).
    """
    admin_url = maintenance_url(url)
    name = database_name(url)
    reset = os.environ.get("SHERPA_TEST_DB_RESET", "") == "1"
    adopt = os.environ.get("SHERPA_TEST_DB_ADOPT", "") == "1"

    if reset and await _database_exists(admin_url, name):
        await _drop_database(admin_url, name)

    if not await _database_exists(admin_url, name):
        await _create_database(admin_url, name)
        _run_migrations(url)
        await _write_marker(url, note="created by the Sherpa test harness (ADR-044)")
        return f"created {name}"

    if not await _has_marker(url):
        if not adopt:
            raise TestDatabaseGuardError(
                f"database {name!r} already exists but carries no {MARKER_TABLE} marker, so "
                "the harness cannot prove it is throwaway and refuses to write to it.\n"
                "If it IS a throwaway test database, adopt it once with:\n"
                "    $env:SHERPA_TEST_DB_ADOPT='1'; uv run pytest\n"
                "or recreate it from scratch with:\n"
                "    $env:SHERPA_TEST_DB_RESET='1'; uv run pytest"
            )
        await _write_marker(url, note="adopted via SHERPA_TEST_DB_ADOPT (ADR-044)")
        _run_migrations(url)
        await _assert_owner_slug_is_free(url, name)
        return f"adopted {name}"

    _run_migrations(url)
    await _assert_owner_slug_is_free(url, name)
    return f"reused {name}"


async def _assert_owner_slug_is_free(url: str, name: str) -> None:
    """Reject a test database whose ``personal`` slug is held by a foreign tenant.

    ``ensure_owner`` seeds the owner with ``ON CONFLICT DO NOTHING``, and ``tenants.slug``
    is unique — so a leftover ``personal`` tenant from a *different* owner identity (e.g. a
    database used before this isolation landed) silently turns the seed into a no-op and
    every API test then dies on an unrelated foreign-key violation. Name the cause instead.
    """
    from app.auth import owner_ids

    tenant_id, _ = owner_ids()
    conn = await _connect(url)
    try:
        if await conn.fetchval("SELECT to_regclass('public.tenants')") is None:
            return
        stale = await conn.fetchval(
            "SELECT tenant_id::text FROM tenants WHERE slug = 'personal' AND tenant_id <> $1",
            tenant_id,
        )
    finally:
        await conn.close()
    if stale is not None:
        raise TestDatabaseGuardError(
            f"test database {name!r} already holds a 'personal' tenant ({stale}) that is not "
            f"the synthetic suite owner ({tenant_id}) — leftover data from before the suite "
            "was isolated. It would silently break owner seeding. Recreate the database:\n"
            "    $env:SHERPA_TEST_DB_RESET='1'; uv run pytest"
        )


# --- L2: fail-closed gate ---------------------------------------------------


async def assert_test_database() -> None:
    """Fail closed unless the connected database carries the harness marker table."""
    from app.config import settings

    global _verified_url
    url = settings.database_url
    if _verified_url == url:
        return

    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as session:
        present = (
            await session.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{MARKER_TABLE}"})
        ).scalar()
    if present is None:
        raise TestDatabaseGuardError(
            f"refusing a destructive operation: database {database_name(url)!r} has no "
            f"{MARKER_TABLE} marker table, so it is not a harness-provisioned test database"
        )
    _verified_url = url


def reset_verification_cache() -> None:
    """Forget the cached positive verification (tests of the guard itself)."""
    global _verified_url
    _verified_url = None


# --- L3: the single destructive entry point ---------------------------------

_RETRYABLE = ("deadlock detected", "lock timeout", "canceling statement due to lock timeout")


async def drop_tenant(tenant_id: uuid.UUID) -> None:
    """Delete one tenant (cascades to its rows) from the verified test database.

    Bounded rather than blind: a `lock_timeout` turns any residual lock contention into a
    named failure instead of a hang, and exactly one retry absorbs a transient deadlock.
    """
    await assert_test_database()

    from sqlalchemy import text

    from app.db import SessionLocal

    for attempt in (1, 2):
        try:
            async with SessionLocal() as session:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                await session.execute(
                    text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tenant_id}
                )
                await session.commit()
            return
        except Exception as exc:  # noqa: BLE001 - re-raised below unless retryable
            message = str(exc).lower()
            if attempt == 2 or not any(marker in message for marker in _RETRYABLE):
                raise
            await asyncio.sleep(0.25)


async def drop_owner_tenant() -> None:
    """Delete the *synthetic* owner tenant — the suite's standard clean slate."""
    from app.auth import owner_ids

    tenant_id, _ = owner_ids()
    await drop_tenant(tenant_id)
