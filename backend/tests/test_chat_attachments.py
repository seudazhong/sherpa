"""Chat attachments: admission, assembly, degradation, provider translation (ADR-043).

Integration where a database is needed (seed + rollback, never destructive), pure
otherwise. Covers the four honest-degradation paths the design promises — no vision,
budget spent, oversized image, vanished node — plus the regression that a text-only
turn keeps its plain-string `content` (so an existing session's cached prefix stays
byte-stable, docs/04 invariant ⑤).
"""

from __future__ import annotations

import base64
import uuid

import pytest

from app.core.admission import PromptConflict, admit_prompt
from app.core.attachments import (
    AssemblyBudget,
    AttachmentRef,
    render_attachment_content,
    resolve_attachments,
)
from app.core.history import assemble_provider_history
from app.db import SessionLocal, ping_db
from app.models import Part, Tenant, User
from app.models import Session as SessionModel
from app.providers.anthropic import _translate as anthropic_translate
from app.providers.content import ImageBlock, TextBlock, normalize_content
from app.providers.gemini import _translate as gemini_translate
from app.services import drive as drive_svc
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound, TooLarge

# A 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def _seed(s) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    tid, uid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
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
        )
    )
    await s.flush()
    return tid, uid, sid


def _ctx(tid: uuid.UUID, uid: uuid.UUID) -> CallerContext:
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")  # type: ignore[arg-type]


# --- resolution (admission side) -------------------------------------------


@pytest.mark.asyncio
async def test_resolve_classifies_image_and_file_and_pins_version() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _ = await _seed(s)
            ctx = _ctx(tid, uid)
            img = await drive_svc.upload(
                s, ctx, parent_id=None, name="shot.png", data=PNG, content_type="image/png"
            )
            doc = await drive_svc.upload(
                s, ctx, parent_id=None, name="notes.md", data=b"# hi", content_type="text/markdown"
            )

            resolved = await resolve_attachments(
                s,
                tenant_id=tid,
                user_id=uid,
                refs=[AttachmentRef(img.id), AttachmentRef(doc.id)],
            )
            assert [a.kind for a in resolved] == ["image", "file_ref"]
            assert resolved[0].version == img.version  # pinned to the current version
            assert resolved[0].payload()["drive_node_id"] == str(img.id)
            assert "data" not in resolved[0].payload()  # a reference, never bytes
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_resolve_rejects_unknown_trashed_and_overcount() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _ = await _seed(s)
            ctx = _ctx(tid, uid)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="a.txt", data=b"a", content_type="text/plain"
            )

            with pytest.raises(NotFound):
                await resolve_attachments(
                    s, tenant_id=tid, user_id=uid, refs=[AttachmentRef(uuid.uuid4())]
                )

            with pytest.raises(Invalid):
                await resolve_attachments(
                    s,
                    tenant_id=tid,
                    user_id=uid,
                    refs=[AttachmentRef(node.id, version=99)],
                )

            with pytest.raises(Invalid):
                await resolve_attachments(
                    s,
                    tenant_id=tid,
                    user_id=uid,
                    refs=[AttachmentRef(node.id) for _ in range(9)],
                )

            await drive_svc.trash(s, ctx, node.id)
            with pytest.raises(NotFound):
                await resolve_attachments(
                    s, tenant_id=tid, user_id=uid, refs=[AttachmentRef(node.id)]
                )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_resolve_rejects_oversized_image() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.config import settings

    async with SessionLocal() as s:
        try:
            tid, uid, _ = await _seed(s)
            ctx = _ctx(tid, uid)
            big = b"\x89PNG" + b"0" * (settings.chat_attachment_max_image_bytes + 1)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="big.png", data=big, content_type="image/png"
            )
            with pytest.raises(TooLarge):
                await resolve_attachments(
                    s, tenant_id=tid, user_id=uid, refs=[AttachmentRef(node.id)]
                )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_cross_owner_attachment_is_not_found() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid_a, uid_a, _ = await _seed(s)
            tid_b, uid_b, _ = await _seed(s)
            node = await drive_svc.upload(
                s, _ctx(tid_a, uid_a), parent_id=None, name="mine.txt", data=b"x"
            )
            with pytest.raises(NotFound):
                await resolve_attachments(
                    s, tenant_id=tid_b, user_id=uid_b, refs=[AttachmentRef(node.id)]
                )
        finally:
            await s.rollback()


# --- admission --------------------------------------------------------------


@pytest.mark.asyncio
async def test_admission_persists_attachment_parts_and_is_idempotent() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from sqlalchemy import select

    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed(s)
            node = await drive_svc.upload(
                s, _ctx(tid, uid), parent_id=None, name="p.png", data=PNG, content_type="image/png"
            )
            cmid = uuid.uuid4()
            adm = await admit_prompt(
                s,
                tenant_id=tid,
                session_id=sid,
                user_id=uid,
                client_message_id=cmid,
                text="what is this?",
                attachments=[AttachmentRef(node.id)],
            )
            parts = (
                (
                    await s.execute(
                        select(Part)
                        .where(Part.tenant_id == tid, Part.message_id == adm.message_id)
                        .order_by(Part.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            assert [p.kind for p in parts] == ["text", "image"]
            assert parts[1].content_redacted["drive_node_id"] == str(node.id)

            # Same body + same attachments ⇒ the original admission is reused.
            again = await admit_prompt(
                s,
                tenant_id=tid,
                session_id=sid,
                user_id=uid,
                client_message_id=cmid,
                text="what is this?",
                attachments=[AttachmentRef(node.id)],
            )
            assert again.reused and again.message_id == adm.message_id

            # Same id, different attachment set ⇒ conflict (409).
            with pytest.raises(PromptConflict):
                await admit_prompt(
                    s,
                    tenant_id=tid,
                    session_id=sid,
                    user_id=uid,
                    client_message_id=cmid,
                    text="what is this?",
                    attachments=[],
                )
        finally:
            await s.rollback()


# --- assembly ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_assembly_shapes_text_only_and_image_turns() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, sid = await _seed(s)
            await admit_prompt(
                s,
                tenant_id=tid,
                session_id=sid,
                user_id=uid,
                client_message_id=uuid.uuid4(),
                text="plain turn",
            )
            history = await assemble_provider_history(s, tid, sid)
            # Regression: a turn without attachments keeps the plain-string shape.
            assert history == [{"role": "user", "content": "plain turn"}]

            node = await drive_svc.upload(
                s, _ctx(tid, uid), parent_id=None, name="p.png", data=PNG, content_type="image/png"
            )
            await admit_prompt(
                s,
                tenant_id=tid,
                session_id=sid,
                user_id=uid,
                client_message_id=uuid.uuid4(),
                text="and this one",
                attachments=[AttachmentRef(node.id)],
            )
            history = await assemble_provider_history(s, tid, sid)
            blocks = history[-1]["content"]
            assert isinstance(blocks, list)
            assert blocks[0] == {"type": "text", "text": "and this one"}
            url = blocks[1]["image_url"]["url"]  # type: ignore[index]
            assert url.startswith("data:image/png;base64,")
            assert base64.b64decode(url.split(",", 1)[1]) == PNG

            # Without vision the same turn degrades to an honest placeholder.
            degraded = await assemble_provider_history(s, tid, sid, supports_vision=False)
            text = degraded[-1]["content"][1]["text"]  # type: ignore[index]
            assert "cannot see images" in text
            assert "p.png" in text
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_text_attachment_is_bounded_and_binary_is_a_pointer() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.config import settings

    async with SessionLocal() as s:
        try:
            tid, uid, _ = await _seed(s)
            ctx = _ctx(tid, uid)
            long_text = b"x" * (settings.chat_attachment_text_extract_bytes + 100)
            doc = await drive_svc.upload(
                s, ctx, parent_id=None, name="log.txt", data=long_text, content_type="text/plain"
            )
            binary = await drive_svc.upload(
                s,
                ctx,
                parent_id=None,
                name="thing.bin",
                data=b"\x00\x01\x02",
                content_type="application/octet-stream",
            )
            resolved = await resolve_attachments(
                s,
                tenant_id=tid,
                user_id=uid,
                refs=[AttachmentRef(doc.id), AttachmentRef(binary.id)],
            )

            budget = AssemblyBudget()
            text_block = await render_attachment_content(
                s,
                tenant_id=tid,
                user_id=uid,
                attachment=resolved[0],
                budget=budget,
                supports_vision=True,
            )
            body = str(text_block["text"])
            assert "truncated to the first" in body
            assert len(body) < len(long_text)

            pointer = await render_attachment_content(
                s,
                tenant_id=tid,
                user_id=uid,
                attachment=resolved[1],
                budget=budget,
                supports_vision=True,
            )
            assert "drive.read" in str(pointer["text"])
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_assembly_budget_degrades_instead_of_growing() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _ = await _seed(s)
            node = await drive_svc.upload(
                s, _ctx(tid, uid), parent_id=None, name="p.png", data=PNG, content_type="image/png"
            )
            resolved = await resolve_attachments(
                s, tenant_id=tid, user_id=uid, refs=[AttachmentRef(node.id)]
            )
            block = await render_attachment_content(
                s,
                tenant_id=tid,
                user_id=uid,
                attachment=resolved[0],
                budget=AssemblyBudget(limit=1),  # already spent
                supports_vision=True,
            )
            assert "budget is spent" in str(block["text"])
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_purged_attachment_degrades_not_crashes() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _ = await _seed(s)
            ctx = _ctx(tid, uid)
            node = await drive_svc.upload(
                s, ctx, parent_id=None, name="gone.png", data=PNG, content_type="image/png"
            )
            resolved = await resolve_attachments(
                s, tenant_id=tid, user_id=uid, refs=[AttachmentRef(node.id)]
            )
            await drive_svc.trash(s, ctx, node.id)
            await drive_svc.purge(s, ctx, node.id)

            block = await render_attachment_content(
                s,
                tenant_id=tid,
                user_id=uid,
                attachment=resolved[0],
                budget=AssemblyBudget(),
                supports_vision=True,
            )
            assert "no longer available" in str(block["text"])
        finally:
            await s.rollback()


# --- provider translation (pure) --------------------------------------------


def test_normalize_content_handles_string_list_and_bad_blocks() -> None:
    assert normalize_content("hi") == [TextBlock("hi")]
    blocks = normalize_content(
        [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ]
    )
    assert blocks[0] == TextBlock("look")
    assert blocks[1] == ImageBlock(media_type="image/png", data_b64="AAA")
    assert isinstance(blocks[2], TextBlock) and "omitted" in blocks[2].text


def test_anthropic_translates_image_blocks() -> None:
    _, msgs = anthropic_translate(
        [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                ],
            },
        ]
    )
    blocks = msgs[0]["content"]
    assert blocks[0] == {"type": "text", "text": "what is this?"}
    assert blocks[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
    }


def test_gemini_translates_image_blocks() -> None:
    _, contents = gemini_translate(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                ],
            }
        ]
    )
    parts = contents[0]["parts"]
    assert parts[0] == {"text": "hi"}
    assert parts[1] == {"inlineData": {"mimeType": "image/jpeg", "data": "QUJD"}}


def test_text_only_translation_is_unchanged() -> None:
    _, msgs = anthropic_translate([{"role": "user", "content": "plain"}])
    assert msgs == [{"role": "user", "content": [{"type": "text", "text": "plain"}]}]
    _, contents = gemini_translate([{"role": "user", "content": "plain"}])
    assert contents == [{"role": "user", "parts": [{"text": "plain"}]}]
