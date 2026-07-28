"""Model provider registry service (ADR-041, MP.1): AEAD key seal/open roundtrip, CRUD,
duplicate-name conflict, default selection, test-result write-back, per-conversation model
override + resolution.

Integration test — skips without a database (needs migration 0031). Rolls back.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.db import SessionLocal, ping_db
from app.models import Session as SessionModel
from app.models import Tenant, User
from app.services import model_providers as svc
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound


async def _seed(s) -> CallerContext:  # type: ignore[no-untyped-def]
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    return CallerContext(tenant_id=tid, user_id=uid, actor="user")


async def _session(s, ctx: CallerContext) -> uuid.UUID:  # type: ignore[no-untyped-def]
    sid = uuid.uuid4()
    s.add(
        SessionModel(
            tenant_id=ctx.tenant_id,
            id=sid,
            user_id=ctx.user_id,
            umo_key=f"web:chat:{sid}",
            channel="web",
            channel_installation_id="local",
            scope_type="chat",
            external_scope_id=str(sid),
            status="open",
        )
    )
    await s.flush()
    return sid


@pytest.mark.asyncio
async def test_create_seals_key_and_first_is_default() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            p = await svc.create_provider(
                s,
                ctx,
                kind="openai_compatible",
                display_name="OpenAI",
                api_key="sk-secret-123",
                base_url="https://api.openai.com/v1",
            )
            assert p.is_default is True
            assert p.status == "pending"
            # Key is AEAD-sealed (ciphertext != plaintext) and decrypts back.
            assert p.token_enc is not None and b"sk-secret-123" not in p.token_enc
            assert svc.open_key(p) == "sk-secret-123"

            # A second source is NOT auto-default.
            p2 = await svc.create_provider(
                s, ctx, kind="anthropic", display_name="Anthropic", api_key="sk-ant-x"
            )
            assert p2.is_default is False
            assert svc.open_key(p2) == "sk-ant-x"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_duplicate_name_conflict_and_validation() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            await svc.create_provider(s, ctx, kind="gemini", display_name="G", api_key="k")
            with pytest.raises(Conflict):
                await svc.create_provider(s, ctx, kind="gemini", display_name="G", api_key="k2")
            with pytest.raises(Invalid):
                await svc.create_provider(s, ctx, kind="bogus", display_name="B", api_key="k")
            with pytest.raises(Invalid):
                await svc.create_provider(s, ctx, kind="gemini", display_name="H", api_key="")
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_update_reseals_key_and_resets_status() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            p = await svc.create_provider(
                s, ctx, kind="openai_compatible", display_name="X", api_key="old-key"
            )
            await svc.record_test_result(s, ctx, provider_id=p.id, ok=True, models=["m1"])
            assert p.status == "active"
            await svc.update_provider(
                s, ctx, provider_id=p.id, api_key="new-key", base_url="https://h/v1"
            )
            assert svc.open_key(p) == "new-key"
            assert p.base_url == "https://h/v1"
            assert p.status == "pending"  # key change re-arms a test
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_set_default_moves_the_flag() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            a = await svc.create_provider(
                s, ctx, kind="openai_compatible", display_name="A", api_key="k"
            )
            b = await svc.create_provider(s, ctx, kind="anthropic", display_name="B", api_key="k")
            assert a.is_default and not b.is_default
            await svc.set_default(s, ctx, provider_id=b.id)
            await s.refresh(a)
            await s.refresh(b)
            assert b.is_default and not a.is_default
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_record_test_result_success_and_failure() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            p = await svc.create_provider(
                s, ctx, kind="openai_compatible", display_name="X", api_key="k"
            )
            await svc.record_test_result(
                s, ctx, provider_id=p.id, ok=True, models=["gpt-5.1", "gpt-5.1", "o4"]
            )
            assert p.status == "active"
            assert p.models == ["gpt-5.1", "o4"]  # dedup, order-preserving
            assert p.default_model == "gpt-5.1"  # first model becomes default
            await svc.record_test_result(
                s, ctx, provider_id=p.id, ok=False, error_redacted="401 invalid"
            )
            assert p.status == "error"
            assert p.last_error_redacted == "401 invalid"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_session_model_override_and_resolution() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            default = await svc.create_provider(
                s,
                ctx,
                kind="openai_compatible",
                display_name="OpenAI",
                api_key="k",
                default_model="gpt-5.1",
            )
            other = await svc.create_provider(
                s,
                ctx,
                kind="anthropic",
                display_name="Anthropic",
                api_key="k",
                default_model="claude-opus-4-8",
            )
            sid = await _session(s, ctx)

            # No override → resolves to the global default source + its default_model.
            r = await svc.resolve_for_session(s, tenant_id=ctx.tenant_id, session_id=sid)
            assert r is not None and r[0].id == default.id and r[1] == "gpt-5.1"

            # Per-conversation override → that source + the chosen model.
            await svc.set_session_model(
                s, ctx, session_id=sid, model_provider_id=other.id, model="claude-sonnet-4-6"
            )
            sel = await svc.get_session_model(s, ctx, session_id=sid)
            assert sel.model_provider_id == other.id and sel.model == "claude-sonnet-4-6"
            r2 = await svc.resolve_for_session(s, tenant_id=ctx.tenant_id, session_id=sid)
            assert r2 is not None and r2[0].id == other.id and r2[1] == "claude-sonnet-4-6"

            # Clear → back to the default.
            await svc.set_session_model(s, ctx, session_id=sid, model_provider_id=None, model=None)
            r3 = await svc.resolve_for_session(s, tenant_id=ctx.tenant_id, session_id=sid)
            assert r3 is not None and r3[0].id == default.id

            # A session with no default configured + no override → None (env fallback).
            async with SessionLocal() as s2:
                try:
                    ctx2 = await _seed(s2)
                    sid2 = await _session(s2, ctx2)
                    assert (
                        await svc.resolve_for_session(s2, tenant_id=ctx2.tenant_id, session_id=sid2)
                        is None
                    )
                finally:
                    await s2.rollback()
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_effective_model_state_tracks_the_real_precedence() -> None:
    # B-1: the chat header must show what a run WOULD use, not the env default.
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            sid = await _session(s, ctx)

            # Nothing configured → env fallback (tests run with PROVIDER_KIND=mock).
            env_state = await svc.get_session_model_state(s, ctx, session_id=sid)
            assert env_state.effective_source == "env"
            assert env_state.effective_provider_id is None
            assert env_state.effective_kind == settings.provider_kind
            assert env_state.effective_model == (
                "mock" if settings.provider_kind == "mock" else settings.provider_model
            )

            default = await svc.create_provider(
                s,
                ctx,
                kind="openai_compatible",
                display_name="Local litellm",
                api_key="k",
                default_model="gpt-5.5",
            )
            other = await svc.create_provider(
                s,
                ctx,
                kind="anthropic",
                display_name="Anthropic",
                api_key="k",
                default_model="claude-opus-4-8",
            )

            # Global default source, no per-chat override.
            st = await svc.get_session_model_state(s, ctx, session_id=sid)
            assert st.effective_source == "default"
            assert st.effective_provider_id == default.id
            assert st.effective_provider_name == "Local litellm"
            assert st.effective_model == "gpt-5.5"
            assert st.model_provider_id is None  # nothing pinned to the session

            # Per-conversation override wins and is reported as such.
            await svc.set_session_model(
                s, ctx, session_id=sid, model_provider_id=other.id, model="claude-sonnet-4-6"
            )
            st2 = await svc.get_session_model_state(s, ctx, session_id=sid)
            assert st2.effective_source == "session"
            assert st2.effective_provider_id == other.id
            assert st2.effective_model == "claude-sonnet-4-6"

            # A disabled override falls back to the default source, and says "default".
            other.enabled = False
            await s.flush()
            st3 = await svc.get_session_model_state(s, ctx, session_id=sid)
            assert st3.model_provider_id == other.id  # the stored selection is unchanged
            assert st3.effective_source == "default"
            assert st3.effective_provider_id == default.id
            assert st3.effective_model == "gpt-5.5"
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_delete_and_not_found() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            p = await svc.create_provider(s, ctx, kind="gemini", display_name="G", api_key="k")
            await svc.delete_provider(s, ctx, provider_id=p.id)
            with pytest.raises(NotFound):
                await svc.get_provider(s, ctx, provider_id=p.id)
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_provider_for_session_builds_adapter_by_kind() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    from app.providers import (
        AnthropicProvider,
        MockProvider,
        OpenAICompatibleProvider,
    )

    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            oai = await svc.create_provider(
                s,
                ctx,
                kind="openai_compatible",
                display_name="OpenAI",
                api_key="k",
                base_url="https://api.openai.com/v1",
                default_model="gpt-5.1",
            )
            anth = await svc.create_provider(
                s,
                ctx,
                kind="anthropic",
                display_name="Anthropic",
                api_key="k",
                default_model="claude-opus-4-8",
            )
            sid = await _session(s, ctx)

            # No override → global default (openai_compatible).
            prov = await svc.provider_for_session(s, tenant_id=ctx.tenant_id, session_id=sid)
            assert isinstance(prov, OpenAICompatibleProvider)
            assert prov._model == "gpt-5.1"  # type: ignore[attr-defined]

            # Per-chat override → native Anthropic adapter.
            await svc.set_session_model(
                s, ctx, session_id=sid, model_provider_id=anth.id, model="claude-sonnet-4-6"
            )
            prov2 = await svc.provider_for_session(s, tenant_id=ctx.tenant_id, session_id=sid)
            assert isinstance(prov2, AnthropicProvider)
            assert prov2._model == "claude-sonnet-4-6"  # type: ignore[attr-defined]

            # A session with no configured providers → env fallback (mock in tests).
            async with SessionLocal() as s2:
                try:
                    ctx2 = await _seed(s2)
                    sid2 = await _session(s2, ctx2)
                    prov3 = await svc.provider_for_session(
                        s2, tenant_id=ctx2.tenant_id, session_id=sid2
                    )
                    assert isinstance(prov3, MockProvider)
                finally:
                    await s2.rollback()
            _ = oai
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_test_connection_success_and_failure() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    import httpx

    async with SessionLocal() as s:
        try:
            ctx = await _seed(s)
            p = await svc.create_provider(
                s,
                ctx,
                kind="openai_compatible",
                display_name="X",
                api_key="k",
                base_url="https://api.openai.com/v1",
            )

            def ok_handler(_r: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"data": [{"id": "gpt-5.1"}, {"id": "o4"}]})

            p = await svc.test_connection(
                s, ctx, provider_id=p.id, transport=httpx.MockTransport(ok_handler)
            )
            assert p.status == "active"
            assert p.models == ["gpt-5.1", "o4"]
            assert p.default_model == "gpt-5.1"

            def fail_handler(_r: httpx.Request) -> httpx.Response:
                return httpx.Response(401, json={"error": {"message": "bad key"}})

            p = await svc.test_connection(
                s, ctx, provider_id=p.id, transport=httpx.MockTransport(fail_handler)
            )
            assert p.status == "error"
            assert p.last_error_redacted and "401" in p.last_error_redacted
        finally:
            await s.rollback()
