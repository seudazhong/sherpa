"""P5 human effective-tree file REST over the same Project overlay as fs_* tools."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport

from app.auth import owner_ids
from app.config import settings
from app.db import SessionLocal, ping_db
from app.main import app
from app.redis_client import ping_redis
from app.services import drive as drive_svc
from app.services import project_workcopy as wc_svc
from app.services.context import CallerContext
from app.tools import ToolContext, build_default_registry
from tests.db_guard import drop_owner_tenant


@pytest.mark.asyncio
async def test_human_file_api_and_agent_tool_share_one_overlay() -> None:
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
                json={"name": "P5 files", "template_id": "python-basic"},
                headers=headers,
            )
            project_id = project.json()["id"]
            chat = await client.post(
                f"/projects/{project_id}/chats",
                json={"title": "edit"},
                headers=headers,
            )
            session_id = chat.json()["id"]

            tree = await client.get(f"/sessions/{session_id}/project-files")
            assert tree.status_code == 200
            assert any(entry["path"] == "main.py" for entry in tree.json()["entries"])

            main = await client.get(
                f"/sessions/{session_id}/project-files/content",
                params={"path": "main.py"},
            )
            assert main.status_code == 200
            assert "hello, sherpa" in main.json()["content"]
            main_hash = main.json()["content_hash"]

            human = await client.put(
                f"/sessions/{session_id}/project-files/content",
                json={
                    "path": "human.txt",
                    "content": "human edit\n",
                    "create_only": True,
                },
                headers=headers,
            )
            assert human.status_code == 200, human.text
            duplicate_create = await client.put(
                f"/sessions/{session_id}/project-files/content",
                json={
                    "path": "human.txt",
                    "content": "overwrite\n",
                    "create_only": True,
                },
                headers=headers,
            )
            assert duplicate_create.status_code == 409

            tenant_id, user_id = owner_ids()
            async with SessionLocal() as session:
                result = (
                    await build_default_registry()
                    .get("fs_write")
                    .execute(
                        ToolContext(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            session_id=uuid.UUID(session_id),
                            run_id=uuid.uuid4(),
                            invocation_id=uuid.uuid4(),
                            session=session,
                        ),
                        {"path": "agent.txt", "content": "agent edit\n"},
                    )
                )
                assert "added agent.txt" in result.llm_content
                await session.commit()

            wc = await client.get(f"/sessions/{session_id}/working-copy")
            assert wc.json()["overlay_entry_count"] == 2
            change_set = await client.get(
                f"/projects/{project_id}/change-sets/{wc.json()['open_change_set_id']}"
            )
            paths = {entry["path"] for entry in change_set.json()["entries"]}
            assert {"human.txt", "agent.txt"} <= paths

            conflict = await client.put(
                f"/sessions/{session_id}/project-files/content",
                json={
                    "path": "main.py",
                    "content": "clobber\n",
                    "if_hash": "0" * 64,
                },
                headers=headers,
            )
            assert conflict.status_code == 409
            updated = await client.put(
                f"/sessions/{session_id}/project-files/content",
                json={
                    "path": "main.py",
                    "content": "print('human')\n",
                    "if_hash": main_hash,
                },
                headers=headers,
            )
            assert updated.status_code == 200

            deleted = await client.delete(
                f"/sessions/{session_id}/project-files/content",
                params={"path": "human.txt"},
                headers=headers,
            )
            assert deleted.status_code == 200

            # Human content reads must refuse an oversized file rather than return a
            # truncated editor buffer that could later overwrite the original.
            cc = CallerContext(tenant_id=tenant_id, user_id=user_id, actor="user")
            async with SessionLocal() as session:
                working_copy = await wc_svc.get_live(session, cc, session_id=uuid.UUID(session_id))
                assert working_copy is not None
                fence = await wc_svc.acquire_lease(session, working_copy, owner="large-file-test")
                raw = b"x" * 1_000_001
                content_hash, _ = await drive_svc.ensure_blob(
                    session,
                    cc,
                    user_id,
                    data=raw,
                    content_type="text/plain",
                )
                await wc_svc.persist_overlay(
                    session,
                    cc,
                    working_copy,
                    fence_token=fence,
                    deltas=[
                        wc_svc.OverlayDelta(
                            path="large.txt",
                            change_kind="added",
                            content_hash=content_hash,
                            size_bytes=len(raw),
                        )
                    ],
                )
                await session.commit()
            too_large = await client.get(
                f"/sessions/{session_id}/project-files/content",
                params={"path": "large.txt"},
            )
            assert too_large.status_code == 413
    finally:
        await drop_owner_tenant()
