"""Host-side fs_* tools over the Project working-copy effective tree (ADR-048)."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import Tenant, User
from app.services import project_workcopy as wc_svc
from app.services import projects as projects_svc
from app.services.context import CallerContext
from app.tools import ToolContext, build_default_registry
from app.tools.base import ToolError


async def _seed(s, *, template_id: str = "python-basic"):  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
    project = await projects_svc.create_project(s, ctx, name="P", template_id=template_id)
    sid = uuid.uuid4()
    s.add(
        SessionModel(
            tenant_id=tid,
            id=sid,
            user_id=uid,
            umo_key=f"web:chat:{sid}",
            channel="web",
            channel_installation_id="local",
            scope_type="chat",
            external_scope_id=str(sid),
            status="open",
            project_id=project.id,
            admitted_seq=1,
        )
    )
    await s.flush()
    return tid, uid, sid, ctx


def _tool_ctx(s, tid, uid, sid):  # type: ignore[no-untyped-def]
    return ToolContext(
        tenant_id=tid,
        user_id=uid,
        session_id=sid,
        run_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        session=s,
    )


@pytest.mark.asyncio
async def test_fs_tools_work_with_sandbox_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not await ping_db():
        pytest.skip("database not reachable")
    monkeypatch.setattr(settings, "sandbox_kind", "disabled")
    async with SessionLocal() as s:
        try:
            tid, uid, sid, ctx = await _seed(s)
            reg = build_default_registry()
            tctx = _tool_ctx(s, tid, uid, sid)

            listing = await reg.get("fs_list").execute(tctx, {})
            assert "main.py" in listing.llm_content
            assert await wc_svc.get_live(s, ctx, session_id=sid) is None

            read = await reg.get("fs_read").execute(tctx, {"path": "main.py"})
            assert "hello, sherpa" in read.llm_content
            assert "sha256=" in read.llm_content

            grep = await reg.get("fs_grep").execute(tctx, {"pattern": "hello", "path": "."})
            assert "main.py" in grep.llm_content

            write = await reg.get("fs_write").execute(
                tctx, {"path": "notes.txt", "content": "alpha\nbeta\n"}
            )
            assert "added notes.txt" in write.llm_content
            assert "Save remains user-only" in write.llm_content

            reread = await reg.get("fs_read").execute(tctx, {"path": "notes.txt"})
            assert "alpha" in reread.llm_content

            edit = await reg.get("fs_edit").execute(
                tctx,
                {
                    "path": "notes.txt",
                    "old_text": "beta",
                    "new_text": "gamma",
                },
            )
            assert "modified notes.txt" in edit.llm_content
            assert (
                "gamma"
                in (await reg.get("fs_read").execute(tctx, {"path": "notes.txt"})).llm_content
            )

            deleted = await reg.get("fs_delete").execute(tctx, {"path": "notes.txt"})
            assert "deleted notes.txt" in deleted.llm_content
            with pytest.raises(ToolError):
                await reg.get("fs_read").execute(tctx, {"path": "notes.txt"})
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_fs_write_hash_guard_and_edit_occurrence_are_zero_write() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid, _ctx = await _seed(s)
            reg = build_default_registry()
            tctx = _tool_ctx(s, tid, uid, sid)
            await reg.get("fs_write").execute(tctx, {"path": "guard.txt", "content": "same same\n"})

            with pytest.raises(ToolError, match="content_hash_mismatch"):
                await reg.get("fs_write").execute(
                    tctx,
                    {
                        "path": "guard.txt",
                        "content": "clobber\n",
                        "if_hash": "0" * 64,
                    },
                )
            with pytest.raises(ToolError, match="expected 1 occurrences, found 2"):
                await reg.get("fs_edit").execute(
                    tctx,
                    {
                        "path": "guard.txt",
                        "old_text": "same",
                        "new_text": "changed",
                    },
                )
            read = await reg.get("fs_read").execute(tctx, {"path": "guard.txt"})
            assert "same same" in read.llm_content
            assert "clobber" not in read.llm_content
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_fs_revert_to_base_removes_overlay() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid, ctx = await _seed(s)
            reg = build_default_registry()
            tctx = _tool_ctx(s, tid, uid, sid)
            project = (await s.get(SessionModel, (tid, sid))).project_id
            assert project is not None
            _entry, original_bytes = await projects_svc.read_file(
                s, ctx, project_id=project, path="main.py"
            )
            original_text = original_bytes.decode("utf-8")

            await reg.get("fs_write").execute(
                tctx, {"path": "main.py", "content": "print('changed')\n"}
            )
            await reg.get("fs_write").execute(tctx, {"path": "main.py", "content": original_text})
            wc = await wc_svc.get_live(s, ctx, session_id=sid)
            assert wc is not None
            assert wc.overlay_entry_count == 0
            assert wc.state == "open"
        finally:
            await s.rollback()


def test_fs_tools_are_registered_full_only() -> None:
    reg = build_default_registry()
    for name in ("fs_list", "fs_read", "fs_grep", "fs_write", "fs_edit", "fs_delete"):
        assert reg.is_visible(name, "full")
        assert not reg.is_visible(name, "safe")


@pytest.mark.asyncio
async def test_fs_write_cannot_replace_directory_with_file() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid, _ctx = await _seed(s, template_id="notes")
            reg = build_default_registry()
            tctx = _tool_ctx(s, tid, uid, sid)
            with pytest.raises(ToolError, match="not a regular file"):
                await reg.get("fs_write").execute(
                    tctx, {"path": "notes", "content": "not a directory anymore"}
                )
            listing = await reg.get("fs_list").execute(tctx, {"path": "notes"})
            assert "notes/" in listing.llm_content
            assert "notes/todo.md" in listing.llm_content
        finally:
            await s.rollback()
