"""Chat attachments over REST: prompt admission + transcript exposure (ADR-043, api §4.2).

Skips without Postgres + Redis. Re-login re-seeds the owner (pytest wipes it). Drives the
real client path: upload to Drive → prompt with the node reference → the transcript
reports the attachment; plus the honest error codes (404 unknown/trashed, 422 over-count,
409 same client_message_id with a different attachment set).
"""

from __future__ import annotations

import base64
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

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def _drop_owner() -> None:
    tid, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


@pytest.mark.asyncio
async def test_prompt_with_drive_attachment_round_trip() -> None:
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
            files={"upload": ("shot.png", PNG, "image/png")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        node = up.json()

        session = await client.post("/sessions", json={"title": "att"}, headers=headers)
        sid = session.json()["id"]

        ok = await client.post(
            f"/sessions/{sid}/prompt",
            json={
                "client_message_id": str(uuid.uuid4()),
                "text": "what is in this image?",
                "attachments": [{"drive_node_id": node["id"]}],
            },
            headers=headers,
        )
        assert ok.status_code == 202, ok.text

        # The transcript carries the attachment as metadata (no bytes).
        page = await client.get(f"/sessions/{sid}/messages")
        parts = page.json()["items"][0]["parts"]
        kinds = [p["kind"] for p in parts]
        assert kinds == ["text", "image"]
        att = parts[1]["attachment"]
        assert att["drive_node_id"] == node["id"]
        assert att["name"] == "shot.png" and att["content_type"] == "image/png"
        assert att["version"] == node["version"]
        assert "base64" not in page.text


@pytest.mark.asyncio
async def test_prompt_attachment_error_codes() -> None:
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
        session = await client.post("/sessions", json={"title": "att"}, headers=headers)
        sid = session.json()["id"]

        unknown = await client.post(
            f"/sessions/{sid}/prompt",
            json={
                "client_message_id": str(uuid.uuid4()),
                "text": "hi",
                "attachments": [{"drive_node_id": str(uuid.uuid4())}],
            },
            headers=headers,
        )
        assert unknown.status_code == 404

        up = await client.post(
            "/drive/files",
            files={"upload": ("a.txt", b"hello", "text/plain")},
            headers=headers,
        )
        node_id = up.json()["id"]
        too_many = await client.post(
            f"/sessions/{sid}/prompt",
            json={
                "client_message_id": str(uuid.uuid4()),
                "text": "hi",
                "attachments": [{"drive_node_id": node_id} for _ in range(9)],
            },
            headers=headers,
        )
        assert too_many.status_code == 422

        cmid = str(uuid.uuid4())
        first = await client.post(
            f"/sessions/{sid}/prompt",
            json={
                "client_message_id": cmid,
                "text": "same text",
                "attachments": [{"drive_node_id": node_id}],
            },
            headers=headers,
        )
        assert first.status_code == 202
        replay = await client.post(
            f"/sessions/{sid}/prompt",
            json={"client_message_id": cmid, "text": "same text", "attachments": []},
            headers=headers,
        )
        assert replay.status_code == 409

        # A trashed node is no longer attachable (404, never 403 — api §2.1).
        await client.post(f"/drive/nodes/{node_id}/trash", headers=headers)
        trashed = await client.post(
            f"/sessions/{sid}/prompt",
            json={
                "client_message_id": str(uuid.uuid4()),
                "text": "hi",
                "attachments": [{"drive_node_id": node_id}],
            },
            headers=headers,
        )
        assert trashed.status_code == 404


@pytest.mark.asyncio
async def test_vision_flag_is_patchable_and_reported_per_session() -> None:
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
        session = await client.post("/sessions", json={"title": "v"}, headers=headers)
        sid = session.json()["id"]

        # No configured source ⇒ the env fallback, which is treated as capable.
        assert (await client.get(f"/sessions/{sid}/model")).json()["supports_vision"] is True

        created = await client.post(
            "/providers",
            json={
                "kind": "openai_compatible",
                "display_name": "Local",
                "api_key": "sk-x",
                "default_model": "m1",
                "supports_vision": False,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        assert created.json()["supports_vision"] is False

        state = await client.get(f"/sessions/{sid}/model")
        assert state.json()["supports_vision"] is False

        patched = await client.patch(
            f"/providers/{pid}", json={"supports_vision": True}, headers=headers
        )
        assert patched.json()["supports_vision"] is True
        assert (await client.get(f"/sessions/{sid}/model")).json()["supports_vision"] is True
