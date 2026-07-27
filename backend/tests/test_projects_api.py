"""Projects REST (api.md §10.5; ADR-037 W2a) end-to-end via the ASGI app.

Skips without Postgres + Redis. Re-login re-seeds the owner (pytest wipes it). Drives
list/create(blank+template)/tree/snapshots, GitHub import → 501, archive import → 202
(then the durable job is run inline), Open-in-Chat binding, and project-context.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from app.auth import owner_ids
from app.config import settings
from app.db import SessionLocal, ping_db
from app.main import app
from app.redis_client import ping_redis
from app.services import projects_import as pimp


async def _drop_owner() -> None:
    tid, _ = owner_ids()
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tid})
        await s.commit()


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_projects_rest_flow() -> None:
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

        # Templates list.
        templates = await client.get("/projects/templates")
        assert templates.status_code == 200
        assert any(t["id"] == "python-basic" for t in templates.json())

        # Blank project.
        blank = await client.post("/projects", json={"name": "Blank"}, headers=headers)
        assert blank.status_code == 201, blank.text
        assert blank.json()["import_status"] == "ready"
        assert blank.json()["current_snapshot_id"] is not None

        # Duplicate name → 409.
        dup = await client.post("/projects", json={"name": "Blank"}, headers=headers)
        assert dup.status_code == 409

        # Template project.
        tmpl = await client.post(
            "/projects",
            json={"name": "Templated", "template_id": "python-basic"},
            headers=headers,
        )
        assert tmpl.status_code == 201, tmpl.text
        pid = tmpl.json()["id"]
        assert tmpl.json()["used_bytes"] > 0

        # List.
        listing = await client.get("/projects")
        assert listing.status_code == 200
        names = {p["name"] for p in listing.json()["items"]}
        assert {"Blank", "Templated"} <= names

        # Get + tree + snapshots.
        got = await client.get(f"/projects/{pid}")
        assert got.status_code == 200
        tree = await client.get(f"/projects/{pid}/tree")
        assert tree.status_code == 200
        assert any(e["path"] == "main.py" for e in tree.json()["entries"])
        snaps = await client.get(f"/projects/{pid}/snapshots")
        assert snaps.status_code == 200
        assert snaps.json()[0]["reason"] == "import"

        # Unknown project → 404.
        missing = await client.get(f"/projects/{uuid.uuid4()}")
        assert missing.status_code == 404

        # GitHub import without a connection → 409 (W2b; was 501 in W2a).
        gh = await client.post(
            "/projects/imports",
            json={
                "kind": "github",
                "name": "GH",
                "github": {
                    "repo_external_id": "1",
                    "owner": "o",
                    "repo": "r",
                    "ref_type": "branch",
                    "ref": "main",
                },
            },
            headers=headers,
        )
        assert gh.status_code == 409

        # Archive import → 202 (importing), then run the durable job inline → ready.
        raw = _zip([("README.md", b"# imported"), ("src/x.py", b"print(1)")])
        imp = await client.post(
            "/projects/imports",
            data={"kind": "archive", "name": "Archived"},
            files={"file": ("proj.zip", raw, "application/zip")},
            headers=headers,
        )
        assert imp.status_code == 202, imp.text
        imp_id = imp.json()["id"]
        assert imp.json()["import_status"] == "importing"
        assert imp.json()["current_snapshot_id"] is None

        tid, _ = owner_ids()
        async with SessionLocal() as s:
            reason, _ = await pimp.process_import(
                s, tenant_id=tid, project_id=uuid.UUID(imp_id), lease_owner="test"
            )
            await s.commit()
        assert reason == "done"
        ready = await client.get(f"/projects/{imp_id}")
        assert ready.json()["import_status"] == "ready"
        assert ready.json()["current_snapshot_id"] is not None

        # Missing archive file → 422.
        bad = await client.post(
            "/projects/imports", data={"kind": "archive", "name": "NoFile"}, headers=headers
        )
        assert bad.status_code == 422

        # Open in Chat → a Project-bound session; CSRF required.
        no_csrf = await client.post(f"/projects/{pid}/chats", json={})
        assert no_csrf.status_code == 403
        chat = await client.post(f"/projects/{pid}/chats", json={"title": "talk"}, headers=headers)
        assert chat.status_code == 201, chat.text
        sid = chat.json()["id"]

        # project-context reflects the binding.
        pc = await client.get(f"/sessions/{sid}/project-context")
        assert pc.status_code == 200
        assert pc.json()["project_id"] == pid
        assert pc.json()["project_name"] == "Templated"
        assert pc.json()["bound"] is False

        # General chat (no project) → null context.
        gen = await client.post("/sessions", json={"title": "general"}, headers=headers)
        gsid = gen.json()["id"]
        gpc = await client.get(f"/sessions/{gsid}/project-context")
        assert gpc.json()["project_id"] is None


@pytest.mark.asyncio
async def test_open_in_chat_on_failed_import_is_rejected() -> None:
    """A failed archive import leaves a visible, snapshotless project (ADR-037). Its
    Open in Chat must be refused (422) — the backend defense behind the hidden UI
    control, so a user can never enter a chat bound to a project with no head snapshot."""
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

        # Unsafe archive (path traversal) → import fails without a snapshot.
        raw = _zip([("../escape.txt", b"pwn")])
        imp = await client.post(
            "/projects/imports",
            data={"kind": "archive", "name": "Evil traversal"},
            files={"file": ("evil.zip", raw, "application/zip")},
            headers=headers,
        )
        assert imp.status_code == 202, imp.text
        pid = imp.json()["id"]

        tid, _ = owner_ids()
        async with SessionLocal() as s:
            reason, _ = await pimp.process_import(
                s, tenant_id=tid, project_id=uuid.UUID(pid), lease_owner="test"
            )
            await s.commit()
        assert reason == "unsafe_archive"

        failed = await client.get(f"/projects/{pid}")
        assert failed.json()["import_status"] == "failed"
        assert failed.json()["current_snapshot_id"] is None

        # Open in Chat on the snapshotless project → 422, no bound session created.
        chat = await client.post(f"/projects/{pid}/chats", json={"title": "x"}, headers=headers)
        assert chat.status_code == 422, chat.text
