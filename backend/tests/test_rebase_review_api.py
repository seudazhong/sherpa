"""P5 one-click rebase-review after a durable head_moved conflict."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app.config import settings
from app.db import ping_db
from app.main import app
from app.redis_client import ping_redis
from tests.db_guard import drop_owner_tenant


@pytest.mark.asyncio
async def test_rebase_review_preserves_overlay_on_new_head() -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")
    await drop_owner_tenant()
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            login = await client.post(
                "/auth/login",
                json={
                    "email": settings.owner_email,
                    "password": settings.owner_password,
                },
            )
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}
            project = await client.post(
                "/projects",
                json={"name": "Rebase", "template_id": "python-basic"},
                headers=headers,
            )
            project_id = project.json()["id"]
            ours_chat = await client.post(
                f"/projects/{project_id}/chats",
                json={"title": "ours"},
                headers=headers,
            )
            theirs_chat = await client.post(
                f"/projects/{project_id}/chats",
                json={"title": "theirs"},
                headers=headers,
            )
            ours_id, theirs_id = ours_chat.json()["id"], theirs_chat.json()["id"]

            ours = await client.put(
                f"/sessions/{ours_id}/project-files/content",
                json={
                    "path": "ours.txt",
                    "content": "ours\n",
                    "create_only": True,
                },
                headers=headers,
            )
            ours_wc = ours.json()
            ours_cs = ours_wc["open_change_set_id"]

            theirs = await client.put(
                f"/sessions/{theirs_id}/project-files/content",
                json={
                    "path": "theirs.txt",
                    "content": "theirs\n",
                    "create_only": True,
                },
                headers=headers,
            )
            theirs_cs = theirs.json()["open_change_set_id"]
            saved_theirs = await client.post(
                f"/projects/{project_id}/change-sets/{theirs_cs}/apply",
                json={},
                headers=headers,
            )
            assert saved_theirs.status_code == 200

            conflict = await client.post(
                f"/projects/{project_id}/change-sets/{ours_cs}/apply",
                json={},
                headers=headers,
            )
            assert conflict.status_code == 409
            detail = conflict.json()["detail"]
            assert detail["error"] == "head_moved"

            conflicted = await client.get(f"/sessions/{ours_id}/working-copy")
            assert conflicted.json()["state"] == "conflicted"
            assert conflicted.json()["head_moved"] is True

            rebased = await client.post(
                f"/projects/{project_id}/working-copies/{ours_wc['id']}/rebase-review",
                json={},
                headers=headers,
            )
            assert rebased.status_code == 200, rebased.text
            rebased_wc = rebased.json()
            assert rebased_wc["state"] == "ready_for_review"
            assert rebased_wc["head_moved"] is False
            assert rebased_wc["open_change_set_id"] is not None
            review = await client.get(
                f"/projects/{project_id}/change-sets/{rebased_wc['open_change_set_id']}"
            )
            assert {entry["path"] for entry in review.json()["entries"]} == {"ours.txt"}

            saved_ours = await client.post(
                f"/projects/{project_id}/change-sets/{rebased_wc['open_change_set_id']}/apply",
                json={},
                headers=headers,
            )
            assert saved_ours.status_code == 200
            tree = await client.get(f"/projects/{project_id}/tree")
            paths = {entry["path"] for entry in tree.json()["entries"]}
            assert {"ours.txt", "theirs.txt"} <= paths
    finally:
        await drop_owner_tenant()
