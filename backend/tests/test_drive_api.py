"""Personal Drive REST surface (api.md §10.2; ADR-030 W1).

Drives the real endpoints end-to-end: folder create, multipart upload, list,
download, storage summary, trash/restore, and human-only purge. Integration test —
skips without Postgres + Redis. Re-login re-seeds the owner (pytest wipes it).
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
async def test_drive_rest_end_to_end() -> None:
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

        # Create a folder.
        folder = await client.post("/drive/folders", json={"name": "docs"}, headers=headers)
        assert folder.status_code == 201, folder.text
        folder_id = folder.json()["id"]

        # Upload into it.
        up = await client.post(
            "/drive/files",
            data={"name": "hello.txt", "parent_id": folder_id},
            files={"upload": ("hello.txt", b"hi there", "text/plain")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        node = up.json()
        assert node["size_bytes"] == 8
        node_id = node["id"]

        # List the folder → the file is there.
        listing = await client.get(f"/drive/nodes?parent={folder_id}")
        names = [n["name"] for n in listing.json()["items"]]
        assert "hello.txt" in names

        # Download returns the bytes.
        content = await client.get(f"/drive/nodes/{node_id}/content")
        assert content.status_code == 200
        assert content.content == b"hi there"

        # Storage summary reflects the used bytes.
        storage = await client.get("/drive/storage")
        assert storage.json()["used_bytes"] == 8
        assert storage.json()["available_bytes"] > 0

        # Trash → gone from live listing; restore brings it back.
        tr = await client.post(f"/drive/nodes/{node_id}/trash", headers=headers)
        assert tr.status_code == 200
        listing = await client.get(f"/drive/nodes?parent={folder_id}")
        assert node_id not in [n["id"] for n in listing.json()["items"]]

        rs = await client.post(f"/drive/nodes/{node_id}/restore", headers=headers)
        assert rs.status_code == 200

        # Rename via PATCH (optimistic version).
        cur = rs.json()["version"]
        pv = await client.patch(
            f"/drive/nodes/{node_id}",
            json={"if_version": cur, "name": "hi.txt"},
            headers=headers,
        )
        assert pv.status_code == 200
        assert pv.json()["name"] == "hi.txt"

        # Permanent purge (human) removes it entirely.
        purge = await client.request("DELETE", f"/drive/nodes/{node_id}", headers=headers)
        assert purge.status_code == 204
        gone = await client.get(f"/drive/nodes/{node_id}/content")
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_drive_files_migrated_visible() -> None:
    """A file created via the legacy /files surface stays downloadable there."""
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
            "/files",
            data={"path": "legacy/note.txt"},
            files={"upload": ("note.txt", b"legacy bytes", "text/plain")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        fid = up.json()["id"]
        dl = await client.get(f"/files/{fid}/content")
        assert dl.status_code == 200
        assert dl.content == b"legacy bytes"


@pytest.mark.asyncio
async def test_upload_filename_with_path_is_stored_as_base_name() -> None:
    """A directory-picked upload sends its RELATIVE PATH as the multipart filename.

    ADR-042 expands folders client-side, so the browser posts
    `filename="demo/notes/deep.txt"`; the server must store the base name instead of
    rejecting the name for containing a separator (the 422 found in the B-5 human lane).
    """
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
        folder = await client.post("/drive/folders", json={"name": "demo"}, headers=headers)
        folder_id = folder.json()["id"]

        posix = await client.post(
            "/drive/files",
            data={"parent_id": folder_id},
            files={"upload": ("demo/notes/deep.txt", b"nested", "text/plain")},
            headers=headers,
        )
        assert posix.status_code == 201, posix.text
        assert posix.json()["name"] == "deep.txt"

        windows = await client.post(
            "/drive/files",
            data={"parent_id": folder_id},
            files={"upload": (r"demo\notes\win.txt", b"nested", "text/plain")},
            headers=headers,
        )
        assert windows.status_code == 201, windows.text
        assert windows.json()["name"] == "win.txt"
