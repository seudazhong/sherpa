"""Knowledge base REST (api.md §10.4; ADR-036 KB4).

Drives the real endpoints end-to-end via the ASGI app: add a Drive file → add as a
knowledge source → list → get → search → delete. Integration test — skips without
Postgres + Redis. Re-login re-seeds the owner (pytest wipes it).
"""

from __future__ import annotations

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
async def test_knowledge_rest_crud() -> None:
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

        up = await client.post(
            "/drive/files",
            data={"name": "fin.md"},
            files={"upload": ("fin.md", "# 财务制度\n\n预算审批流程。".encode(), "text/markdown")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        file_id = up.json()["id"]

        add = await client.post("/knowledge/sources", json={"file_id": file_id}, headers=headers)
        assert add.status_code == 201, add.text
        source = add.json()
        assert source["status"] == "queued"
        assert source["display_name"] == "fin.md"
        sid = source["id"]

        listing = await client.get("/knowledge/sources")
        assert listing.status_code == 200
        assert any(s["id"] == sid for s in listing.json())

        got = await client.get(f"/knowledge/sources/{sid}")
        assert got.status_code == 200

        search = await client.post("/knowledge/search", json={"query": "预算审批"})
        assert search.status_code == 200, search.text
        body = search.json()
        assert "retrieval_invocation_id" in body
        assert isinstance(body["hits"], list)
        assert isinstance(body["sufficient"], bool)

        # Search requires CSRF? No — it is a read; but writes do. Delete needs CSRF.
        no_csrf = await client.delete(f"/knowledge/sources/{sid}")
        assert no_csrf.status_code == 403

        deleted = await client.delete(f"/knowledge/sources/{sid}", headers=headers)
        assert deleted.status_code == 204

        gone = await client.get(f"/knowledge/sources/{sid}")
        assert gone.status_code == 404
