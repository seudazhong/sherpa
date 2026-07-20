"""Gmail connector OAuth round-trip (m2-14) with a fake Google client.

login -> connect (PKCE URL) -> callback (fake token exchange) -> connector row
with an encrypted token that unseals -> list -> disconnect. Also invalid state.
Integration test — skips without Postgres + Redis; cleans up the owner tenant.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select, text

from app.auth import owner_ids
from app.config import settings
from app.connectors.gmail import get_gmail_client
from app.db import SessionLocal, ping_db
from app.main import app
from app.models import Connector, Run
from app.redis_client import ping_redis
from app.security import (
    ConnectorSeal,
    ConnectorTokenIdentity,
    connector_vault_capability,
    load_keyring,
    open_connector_token,
)


class _FakeGmail:
    def authorization_url(self, *, state: str, challenge: str) -> str:
        return f"https://accounts.google.test/auth?state={state}&code_challenge={challenge}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, object]:
        return {
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "scope": settings.gmail_scope,
            "expires_in": 3599,
            "token_type": "Bearer",
        }

    async def fetch_email(self, *, access_token: str) -> str:
        return "owner@gmail.com"


async def _drop_owner() -> None:
    tid, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


@pytest.mark.asyncio
async def test_gmail_oauth_round_trip_and_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    app.dependency_overrides[get_gmail_client] = lambda: _FakeGmail()
    await _drop_owner()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            login = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            csrf = login.json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}

            # connect -> PKCE authorization URL carrying the signed state
            r = await client.post(
                "/connectors/gmail/connect",
                json={"return_to": "/connectors", "sync_scope": {"lookback_days": 30}},
                headers=headers,
            )
            assert r.status_code == 200
            auth_url = r.json()["authorization_url"]
            state = parse_qs(urlparse(auth_url).query)["state"][0]

            # invalid state -> 400
            bad = await client.get(
                "/connectors/gmail/oauth/callback", params={"code": "x", "state": "nope.sig"}
            )
            assert bad.status_code == 400

            # callback -> 303 redirect to return_to?gmail=connected
            cb = await client.get(
                "/connectors/gmail/oauth/callback", params={"code": "auth-code", "state": state}
            )
            assert cb.status_code == 303
            assert "gmail=connected" in cb.headers["location"]

            # list -> one active gmail connector
            lst = await client.get("/connectors")
            assert lst.status_code == 200
            items = lst.json()
            assert len(items) == 1
            conn = items[0]
            assert conn["status"] == "active"
            assert conn["account_email"] == "owner@gmail.com"
            assert settings.gmail_scope in conn["granted_scopes"]
            connector_id = conn["id"]

            # durable sync admission -> 202 + a queued gmail_sync run
            enqueued: list[tuple[str, str]] = []

            async def _fake_enqueue(cid: uuid.UUID, rid: uuid.UUID) -> None:
                enqueued.append((str(cid), str(rid)))

            monkeypatch.setattr("app.api.connectors.queue.enqueue_gmail_sync", _fake_enqueue)
            sync = await client.post(f"/connectors/{connector_id}/sync", headers=headers)
            assert sync.status_code == 202
            adm = sync.json()
            assert adm["state"] == "queued"
            assert len(enqueued) == 1 and enqueued[0][0] == connector_id
            tid_run, _ = owner_ids()
            async with SessionLocal() as s:
                run = (
                    await s.execute(
                        select(Run).where(
                            Run.tenant_id == tid_run, Run.id == uuid.UUID(adm["run_id"])
                        )
                    )
                ).scalar_one()
                assert run.run_kind == "gmail_sync" and run.status == "queued"

            # the stored token is encrypted and unseals to the original tokens
            tid, _ = owner_ids()
            async with SessionLocal() as s:
                row = (
                    await s.execute(
                        select(Connector).where(
                            Connector.tenant_id == tid, Connector.id == uuid.UUID(connector_id)
                        )
                    )
                ).scalar_one()
                assert row.token_enc is not None and row.token_enc != b""
                seal = ConnectorSeal(
                    token_enc=row.token_enc,
                    nonce=row.nonce,
                    kek_id=row.kek_id,
                    key_version=row.key_version,
                    token_algorithm=row.token_algorithm,
                    aad_version=row.aad_version,
                )
                identity = ConnectorTokenIdentity(
                    tenant_id=tid,
                    connector_id=row.id,
                    external_account_id=row.external_account_id,
                )
                opened = open_connector_token(
                    seal, identity, connector_vault_capability(), load_keyring()
                )
                assert opened["refresh_token"] == "fake-refresh"

            # disconnect -> revoked, token cleared
            d = await client.delete(f"/connectors/{connector_id}", headers=headers)
            assert d.status_code == 200 and d.json()["status"] == "revoked"
            async with SessionLocal() as s:
                row = await s.get(Connector, (tid, uuid.UUID(connector_id)))
                assert row is not None and row.status == "revoked" and row.token_enc is None
    finally:
        app.dependency_overrides.pop(get_gmail_client, None)
        await _drop_owner()
