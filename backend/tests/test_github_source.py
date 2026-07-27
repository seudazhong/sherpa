"""GitHub source connection lifecycle + read-only REST proxy (ADR-038, W2b).

Integration test — skips without a database (needs migration 0029). Uses a deterministic
GitHub mock transport; rolls back. Covers connect (validate + seal), status, soft-revoke,
repo/ref pickers, and the no-connection guard.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, ping_db
from app.models import GithubConnection, Tenant, User
from app.services import github_source as gh
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid
from tests.github_mock import GithubMock


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


@pytest.mark.asyncio
async def test_connect_seals_and_validates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    mock = GithubMock(login="octocat")
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            token = "github_pat_abcdef"  # noqa: S105
            conn = await gh.create_connection(s, ctx, auth_kind="pat", token=token)
            assert conn.status == "active"
            assert conn.account_login == "octocat"
            # Token is sealed, never stored in plaintext.
            assert conn.token_enc is not None
            assert token.encode() not in (conn.token_enc or b"")

            status = await gh.get_status(s, ctx)
            assert status.connected is True
            assert status.account_login == "octocat"
            assert "contents:read" in status.scopes

            # Soft-revoke wipes the token but keeps the row.
            await gh.delete_connection(s, ctx)
            reloaded = await s.get(GithubConnection, (ctx.tenant_id, conn.id))
            assert reloaded is not None
            assert reloaded.status == "revoked"
            assert reloaded.token_enc is None
            after = await gh.get_status(s, ctx)
            assert after.connected is False
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_connect_rejects_non_fine_grained_prefix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    # GET /user would succeed if reached — prove the prefix guard rejects BEFORE any call.
    mock = GithubMock(login="octocat")
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            for bad in ("ghp_classic", "gho_oauth", "ghs_install", "", "   ", "not-a-token"):
                with pytest.raises(Invalid):
                    await gh.create_connection(s, ctx, auth_kind="pat", token=bad)  # noqa: S106
            # Nothing was validated upstream and nothing was stored.
            assert mock.seen_auth == []
            assert (await gh.get_status(s, ctx)).connected is False
            # The accepted fine-grained prefix still connects.
            conn = await gh.create_connection(
                s,
                ctx,
                auth_kind="pat",
                token="github_pat_ok",  # noqa: S106
            )
            assert conn.status == "active"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_connect_rejects_bad_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    mock = GithubMock(user_status=401)
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            with pytest.raises(Invalid):
                # Valid prefix so we exercise the GET /user (401) rejection path.
                await gh.create_connection(s, ctx, auth_kind="pat", token="github_pat_bad")  # noqa: S106
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_pickers_and_no_connection_guard(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    mock = GithubMock()
    monkeypatch.setattr(gh, "_make_async_client", mock.client_factory())
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            # No connection yet → pickers 409.
            with pytest.raises(Conflict):
                await gh.list_repos(s, ctx, query=None, cursor=None, limit=30)

            await gh.create_connection(s, ctx, auth_kind="pat", token="github_pat_tok")  # noqa: S106
            repos, cursor = await gh.list_repos(s, ctx, query=None, cursor=None, limit=30)
            assert repos and repos[0].repo == "hello"
            assert repos[0].repo_external_id == "123"

            refs = await gh.list_refs(s, ctx, repo_external_id="123", kind=None, query=None)
            names = {(r.ref_type, r.name) for r in refs}
            assert ("branch", "main") in names
            assert ("tag", "v1.0") in names
        finally:
            await s.rollback()
