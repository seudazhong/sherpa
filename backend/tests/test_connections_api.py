"""GitHub connection + one-time import REST (api.md §10.6; ADR-038 W2b) via the ASGI app.

Skips without Postgres + Redis. Re-login re-seeds the owner. Uses a deterministic GitHub
mock transport. Covers connection CRUD (token never returned), repo/ref pickers, the
no-connection guard, the durable github import (202 → run inline → ready + provenance),
and failure + retry.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from app.auth import owner_ids
from app.config import settings
from app.db import SessionLocal, ping_db
from app.main import app
from app.redis_client import ping_redis
from app.services import github_source as gh
from app.services import projects_import as pimp
from tests.github_mock import TEST_OID, GithubMock


async def _drop_owner() -> None:
    tid, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    login = await client.post(
        "/auth/login",
        json={"email": settings.owner_email, "password": settings.owner_password},
    )
    return {"X-CSRF-Token": login.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_connection_crud_never_returns_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    mock = GithubMock(login="octocat")
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        headers = await _login(client)

        # Not connected.
        st = await client.get("/connections/github")
        assert st.status_code == 200
        assert st.json()["connected"] is False

        # CSRF required.
        no_csrf = await client.post("/connections/github", json={"token": "x"})
        assert no_csrf.status_code == 403

        # Connect → 201, status only (never the token).
        created = await client.post(
            "/connections/github", json={"token": "github_pat_secret"}, headers=headers
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["connected"] is True
        assert body["account_login"] == "octocat"
        assert "token" not in body
        assert "github_pat_secret" not in created.text

        st2 = await client.get("/connections/github")
        assert st2.json()["connected"] is True

        # Disconnect.
        deleted = await client.delete("/connections/github", headers=headers)
        assert deleted.status_code == 204
        assert (await client.get("/connections/github")).json()["connected"] is False


@pytest.mark.asyncio
async def test_github_import_rest_flow(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    mock = GithubMock()
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        headers = await _login(client)

        # Import without a connection → 409.
        spec = {
            "kind": "github",
            "name": "GH import",
            "github": {
                "repo_external_id": "123",
                "owner": "octocat",
                "repo": "hello",
                "ref_type": "branch",
                "ref": "main",
            },
        }
        no_conn = await client.post("/projects/imports", json=spec, headers=headers)
        assert no_conn.status_code == 409, no_conn.text

        # Connect, then pickers work.
        await client.post("/connections/github", json={"token": "tok"}, headers=headers)
        repos = await client.get("/projects/github/repos")
        assert repos.status_code == 200
        assert repos.json()["items"][0]["repo_external_id"] == "123"
        refs = await client.get("/projects/github/refs?repo_external_id=123")
        assert refs.status_code == 200
        assert any(r["name"] == "main" for r in refs.json())

        # Import → 202 importing (no snapshot yet).
        imp = await client.post("/projects/imports", json=spec, headers=headers)
        assert imp.status_code == 202, imp.text
        pid = imp.json()["id"]
        assert imp.json()["import_status"] == "importing"
        assert imp.json()["source_status"] == "importing"

        # Run the durable job inline → done.
        tid, _ = owner_ids()
        async with SessionLocal() as s:
            reason, _ = await pimp.process_import(
                s, tenant_id=tid, project_id=uuid.UUID(pid), lease_owner="test"
            )
            await s.commit()
        assert reason == "done"

        # GET surfaces the frozen provenance (source_oid + imported), never a token.
        got = await client.get(f"/projects/{pid}")
        assert got.status_code == 200
        gj = got.json()
        assert gj["import_status"] == "ready"
        assert gj["source_status"] == "imported"
        assert gj["source"]["source_oid"] == TEST_OID
        assert gj["source"]["ref_type"] == "branch"
        assert gj["source"]["status"] == "imported"
        assert "token" not in got.text

        # Retry on a ready project → 409 (nothing to retry).
        retry = await client.post(f"/projects/{pid}/imports/retry", headers=headers)
        assert retry.status_code == 409

        # Tree has the stripped top-level (README at root).
        tree = await client.get(f"/projects/{pid}/tree")
        assert any(e["path"] == "README.md" for e in tree.json()["entries"])


@pytest.mark.asyncio
async def test_github_import_failure_then_retry_rest(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    mock = GithubMock(fail_tarball_times=1)
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        headers = await _login(client)
        await client.post("/connections/github", json={"token": "tok"}, headers=headers)
        spec = {
            "kind": "github",
            "name": "GH retry",
            "github": {
                "repo_external_id": "123",
                "owner": "octocat",
                "repo": "hello",
                "ref_type": "branch",
                "ref": "main",
            },
        }
        imp = await client.post("/projects/imports", json=spec, headers=headers)
        pid = imp.json()["id"]

        tid, _ = owner_ids()
        async with SessionLocal() as s:
            await pimp.process_import(s, tenant_id=tid, project_id=uuid.UUID(pid), lease_owner="t1")
            await s.commit()
        failed = await client.get(f"/projects/{pid}")
        assert failed.json()["import_status"] == "failed"
        assert failed.json()["source_status"] == "import_failed"

        # Retry → 202, then run inline → ready.
        retry = await client.post(f"/projects/{pid}/imports/retry", headers=headers)
        assert retry.status_code == 202, retry.text
        async with SessionLocal() as s:
            reason, _ = await pimp.process_import(
                s, tenant_id=tid, project_id=uuid.UUID(pid), lease_owner="t2"
            )
            await s.commit()
        assert reason == "done"
        assert (await client.get(f"/projects/{pid}")).json()["import_status"] == "ready"
