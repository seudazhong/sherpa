"""Auth + session/message HTTP flow (#10): login -> create session -> prompt ->
list messages, plus authz (401 without cookie, 403 without CSRF, 404 cross-tenant).

Integration test — skips without Postgres + Redis. Uses the deterministic owner
tenant (seeded on login) and cleans it up before/after.
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
    tenant_id, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tenant_id})
        await s.commit()


@pytest.mark.asyncio
async def test_login_session_prompt_messages_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    enqueued: list[uuid.UUID] = []

    async def _fake_enqueue(run_id: uuid.UUID) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.api.prompt.queue.enqueue_run", _fake_enqueue)

    await _drop_owner()
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            # unauthenticated unsafe request -> 401
            r = await client.post("/sessions", json={})
            assert r.status_code == 401

            # wrong credentials -> 401
            r = await client.post(
                "/auth/login", json={"email": settings.owner_email, "password": "nope"}
            )
            assert r.status_code == 401

            # login -> 200 + cookie + csrf
            r = await client.post(
                "/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password},
            )
            assert r.status_code == 200
            auth = r.json()
            csrf = auth["csrf_token"]
            assert client.cookies.get(settings.session_cookie_name)

            # missing CSRF on unsafe request -> 403
            r = await client.post("/sessions", json={})
            assert r.status_code == 403

            # create session -> 201
            r = await client.post("/sessions", json={"title": "Hi"}, headers={"X-CSRF-Token": csrf})
            assert r.status_code == 201
            sess = r.json()
            sid = sess["id"]
            assert sess["channel"] == "web" and sess["title"] == "Hi"

            # list sessions -> contains it
            r = await client.get("/sessions")
            assert r.status_code == 200
            assert sid in [s["id"] for s in r.json()["items"]]

            # prompt -> 202 + enqueue
            r = await client.post(
                f"/sessions/{sid}/prompt",
                json={"client_message_id": str(uuid.uuid4()), "text": "hello there"},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 202
            adm = r.json()
            assert adm["state"] == "queued"
            assert len(enqueued) == 1 and str(enqueued[0]) == adm["run_id"]

            # messages -> the user prompt is present
            r = await client.get(f"/sessions/{sid}/messages")
            assert r.status_code == 200
            page = r.json()
            texts = [p["text"] for m in page["items"] for p in m["parts"]]
            assert "hello there" in texts
            assert page["items"][0]["role"] == "user"

            # cross-tenant / unknown session -> 404
            r = await client.get(f"/sessions/{uuid.uuid4()}/messages")
            assert r.status_code == 404

            # GET /auth/session rotates CSRF; logout with the rotated token -> 204
            r = await client.get("/auth/session")
            assert r.status_code == 200
            new_csrf = r.json()["csrf_token"]
            assert new_csrf != csrf

            r = await client.post("/auth/logout", headers={"X-CSRF-Token": new_csrf})
            assert r.status_code == 204

            # session invalidated -> 401
            r = await client.post("/sessions", json={}, headers={"X-CSRF-Token": new_csrf})
            assert r.status_code == 401
    finally:
        await _drop_owner()
