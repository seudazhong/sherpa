"""Model providers REST (api.md §10.8; ADR-041) end-to-end via the ASGI app.

Skips without Postgres + Redis. Re-login re-seeds the owner (pytest wipes it). Drives
create (write-only key)/list/get/patch/default/session-model/delete + CSRF. The key is
never returned; /test is exercised at the service layer (test_model_providers.py) since it
makes a network call.
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


async def _drop_owner() -> None:
    tid, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


@pytest.mark.asyncio
async def test_model_providers_rest_flow() -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await _drop_owner()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        login = await client.post(
            "/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password},
        )
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        assert (await client.get("/providers")).json() == []

        # Create requires CSRF; the key is write-only (never returned).
        no_csrf = await client.post(
            "/providers",
            json={"kind": "openai_compatible", "display_name": "OpenAI", "api_key": "sk-x"},
        )
        assert no_csrf.status_code == 403
        created = await client.post(
            "/providers",
            json={
                "kind": "openai_compatible",
                "display_name": "OpenAI",
                "api_key": "sk-secret",
                "base_url": "https://api.openai.com/v1",
                "default_model": "gpt-5.1",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["is_default"] is True and body["has_key"] is True
        assert "sk-secret" not in created.text and "api_key" not in body
        oai_id = body["id"]

        # Duplicate name → 409.
        dup = await client.post(
            "/providers", json={"kind": "gemini", "display_name": "OpenAI", "api_key": "k"},
            headers=headers,
        )
        assert dup.status_code == 409

        # A second source (not default).
        anth = await client.post(
            "/providers",
            json={"kind": "anthropic", "display_name": "Anthropic", "api_key": "sk-ant"},
            headers=headers,
        )
        anth_id = anth.json()["id"]
        assert anth.json()["is_default"] is False

        assert len((await client.get("/providers")).json()) == 2

        # Patch default_model.
        patched = await client.patch(
            f"/providers/{oai_id}", json={"default_model": "o4"}, headers=headers
        )
        assert patched.json()["default_model"] == "o4"

        # Move the global default.
        moved = await client.post(f"/providers/{anth_id}/default", headers=headers)
        assert moved.json()["is_default"] is True
        assert (await client.get(f"/providers/{oai_id}")).json()["is_default"] is False

        # Per-conversation model override.
        gen = await client.post("/sessions", json={"title": "chat"}, headers=headers)
        sid = gen.json()["id"]
        assert (await client.get(f"/sessions/{sid}/model")).json()["model_provider_id"] is None
        sel = await client.post(
            f"/sessions/{sid}/model",
            json={"model_provider_id": oai_id, "model": "gpt-5.1"},
            headers=headers,
        )
        assert sel.json()["model_provider_id"] == oai_id and sel.json()["model"] == "gpt-5.1"
        cleared = await client.post(
            f"/sessions/{sid}/model", json={"model_provider_id": None, "model": None},
            headers=headers,
        )
        assert cleared.json()["model_provider_id"] is None

        # Unknown provider → 404; delete works.
        assert (await client.get(f"/providers/{uuid.uuid4()}")).status_code == 404
        assert (await client.delete(f"/providers/{oai_id}", headers=headers)).status_code == 204
        assert len((await client.get("/providers")).json()) == 1
