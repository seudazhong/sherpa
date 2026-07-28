"""Model provider registry capability layer (ADR-041).

Owner-configured multi-source model providers. One row = one source (OpenAI / Anthropic /
Gemini / DeepSeek / Qwen / …): ``kind`` + ``base_url`` + an **AEAD-sealed API key** +
model catalog + a global-default flag. The key is sealed under the active KEK
(:mod:`app.security.model_provider_key`) and decrypted ONLY at the ``Provider.stream()`` /
test-connection boundary (``open_key``), gated by the connector-vault capability — never
logged, never returned in a REST response. Per-conversation model override lives on
``sessions`` (``model_provider_id`` + ``model``).

Provider configuration is a **human Settings action**; there is no agent tool (ADR-041).
Every query is scoped by ``tenant_id`` AND ``user_id`` (ADR-015). The caller owns the
transaction (services flush, never commit).
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ModelProvider
from app.models import Session as SessionModel
from app.providers import Provider, build_from_config, build_provider
from app.providers.anthropic import _DEFAULT_BASE as _ANTHROPIC_BASE
from app.providers.gemini import _DEFAULT_BASE as _GEMINI_BASE
from app.providers.openai_compatible import openai_endpoint
from app.security.keyring import load_keyring
from app.security.model_provider_key import (
    ModelProviderKeyIdentity,
    ModelProviderSeal,
    open_model_provider_key,
    seal_model_provider_key,
)
from app.security.redaction import redact
from app.security.vault import connector_vault_capability
from app.services.context import CallerContext
from app.services.errors import Conflict, Invalid, NotFound

_KINDS = ("openai_compatible", "anthropic", "gemini")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("model providers require a user context")
    return ctx.user_id


def _validate(kind: str, display_name: str) -> str:
    if kind not in _KINDS:
        raise Invalid(f"unsupported kind: {kind}")
    name = display_name.strip()
    if not name or len(name) > 200:
        raise Invalid("display_name must be 1..200 characters")
    return name


# --- AEAD key seal/open -----------------------------------------------------


def _identity(p: ModelProvider) -> ModelProviderKeyIdentity:
    return ModelProviderKeyIdentity(
        tenant_id=p.tenant_id, provider_id=p.id, user_id=p.user_id, kind=p.kind
    )


def _apply_seal(p: ModelProvider, key: str) -> None:
    seal = seal_model_provider_key(key, _identity(p), load_keyring())
    p.token_enc = seal.token_enc
    p.nonce = seal.nonce
    p.kek_id = seal.kek_id
    p.key_version = seal.key_version
    p.token_algorithm = seal.token_algorithm
    p.aad_version = seal.aad_version


def open_key(p: ModelProvider) -> str | None:
    """Decrypt a provider's API key at the connector boundary (never logged). None when
    no key is set. Only ``build_provider`` / test-connection should call this."""
    if p.token_enc is None or p.nonce is None or p.kek_id is None or p.key_version is None:
        return None
    seal = ModelProviderSeal(
        token_enc=p.token_enc,
        nonce=p.nonce,
        kek_id=p.kek_id,
        key_version=p.key_version,
        token_algorithm=p.token_algorithm or "AES-256-GCM",
        aad_version=p.aad_version or 1,
    )
    return open_model_provider_key(seal, _identity(p), connector_vault_capability(), load_keyring())


# --- CRUD -------------------------------------------------------------------


async def _by_name(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, name: str
) -> ModelProvider | None:
    return await db.scalar(
        select(ModelProvider).where(
            ModelProvider.tenant_id == ctx.tenant_id,
            ModelProvider.user_id == uid,
            ModelProvider.display_name == name,
        )
    )


async def list_providers(db: AsyncSession, ctx: CallerContext) -> list[ModelProvider]:
    uid = _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(ModelProvider)
                .where(ModelProvider.tenant_id == ctx.tenant_id, ModelProvider.user_id == uid)
                .order_by(ModelProvider.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_provider(
    db: AsyncSession, ctx: CallerContext, *, provider_id: uuid.UUID
) -> ModelProvider:
    uid = _require_user(ctx)
    p = await db.get(ModelProvider, (ctx.tenant_id, provider_id))
    if p is None or p.user_id != uid:
        raise NotFound("model provider not found")
    return p


async def create_provider(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    kind: str,
    display_name: str,
    api_key: str,
    base_url: str | None = None,
    default_model: str | None = None,
) -> ModelProvider:
    """Create + AEAD-seal a model source. The first source an owner adds becomes the
    global default. Duplicate ``display_name`` ⇒ 409. Caller commits."""
    uid = _require_user(ctx)
    name = _validate(kind, display_name)
    if not (api_key or "").strip():
        raise Invalid("api_key is required")
    if await _by_name(db, ctx, uid, name) is not None:
        raise Conflict("a provider with that name already exists")

    existing_any = await db.scalar(
        select(ModelProvider.id).where(
            ModelProvider.tenant_id == ctx.tenant_id, ModelProvider.user_id == uid
        )
    )
    p = ModelProvider(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        kind=kind,
        display_name=name,
        base_url=(base_url or None),
        models=[],
        default_model=(default_model or None),
        enabled=True,
        is_default=existing_any is None,  # first source is the default
        status="pending",
    )
    _apply_seal(p, api_key.strip())
    db.add(p)
    await db.flush()
    await db.refresh(p, ["created_at", "updated_at"])
    return p


async def update_provider(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    provider_id: uuid.UUID,
    display_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    default_model: str | None = None,
    enabled: bool | None = None,
) -> ModelProvider:
    """Patch a source (re-seal the key when ``api_key`` is present). Caller commits."""
    uid = _require_user(ctx)
    p = await get_provider(db, ctx, provider_id=provider_id)
    if display_name is not None:
        name = _validate(p.kind, display_name)
        other = await _by_name(db, ctx, uid, name)
        if other is not None and other.id != p.id:
            raise Conflict("a provider with that name already exists")
        p.display_name = name
    if base_url is not None:
        p.base_url = base_url.strip() or None
    if default_model is not None:
        p.default_model = default_model.strip() or None
    if enabled is not None:
        p.enabled = enabled
    if api_key is not None:
        if not api_key.strip():
            raise Invalid("api_key cannot be empty")
        _apply_seal(p, api_key.strip())
        p.status = "pending"  # re-test after a key change
        p.last_error_redacted = None
    await db.flush()
    await db.refresh(p, ["updated_at"])
    return p


async def delete_provider(db: AsyncSession, ctx: CallerContext, *, provider_id: uuid.UUID) -> None:
    p = await get_provider(db, ctx, provider_id=provider_id)
    await db.delete(p)
    await db.flush()


async def set_default(
    db: AsyncSession, ctx: CallerContext, *, provider_id: uuid.UUID
) -> ModelProvider:
    """Make a source the single global default (atomically clears the prior default).
    Caller commits."""
    uid = _require_user(ctx)
    p = await get_provider(db, ctx, provider_id=provider_id)
    # Clear any existing default first (partial-unique index forbids two defaults).
    await db.execute(
        update(ModelProvider)
        .where(
            ModelProvider.tenant_id == ctx.tenant_id,
            ModelProvider.user_id == uid,
            ModelProvider.is_default.is_(True),
        )
        .values(is_default=False)
    )
    await db.flush()
    p.is_default = True
    await db.flush()
    await db.refresh(p, ["updated_at"])
    return p


# --- test-connection / catalog write-back -----------------------------------


async def record_test_result(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    provider_id: uuid.UUID,
    ok: bool,
    models: list[str] | None = None,
    error_redacted: str | None = None,
) -> ModelProvider:
    """Persist a test-connection outcome: active + catalog on success, error + redacted
    reason on failure. Caller commits. (The network fetch itself lives in the adapter/REST
    layer, MP.3, which decrypts the key via ``open_key`` server-side.)"""
    p = await get_provider(db, ctx, provider_id=provider_id)
    if ok:
        p.status = "active"
        p.last_error_redacted = None
        if models is not None:
            p.models = list(dict.fromkeys(models))  # dedup, keep order
            if p.default_model is None and p.models:
                p.default_model = p.models[0]
    else:
        p.status = "error"
        p.last_error_redacted = (error_redacted or "connection failed")[:500]
    await db.flush()
    return p


# --- per-conversation selection ---------------------------------------------


@dataclasses.dataclass(frozen=True)
class SessionSelection:
    model_provider_id: uuid.UUID | None
    model: str | None


async def get_session_model(
    db: AsyncSession, ctx: CallerContext, *, session_id: uuid.UUID
) -> SessionSelection:
    uid = _require_user(ctx)
    session = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if session is None or session.user_id != uid or session.status == "deleted":
        raise NotFound("session not found")
    return SessionSelection(model_provider_id=session.model_provider_id, model=session.model)


async def set_session_model(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    session_id: uuid.UUID,
    model_provider_id: uuid.UUID | None,
    model: str | None,
) -> SessionSelection:
    """Set/clear the per-conversation model override. ``None`` provider ⇒ fall back to the
    global default. A switch carries BOTH the source id and the model. Caller commits."""
    uid = _require_user(ctx)
    session = await db.get(SessionModel, (ctx.tenant_id, session_id))
    if session is None or session.user_id != uid or session.status == "deleted":
        raise NotFound("session not found")
    if model_provider_id is not None:
        # Validate the source belongs to the owner (raises NotFound otherwise).
        await get_provider(db, ctx, provider_id=model_provider_id)
        session.model_provider_id = model_provider_id
        session.model = model or None
    else:
        session.model_provider_id = None
        session.model = None
    await db.flush()
    return SessionSelection(model_provider_id=session.model_provider_id, model=session.model)


async def default_provider(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> ModelProvider | None:
    """The owner's global-default enabled source, if any."""
    return await db.scalar(
        select(ModelProvider).where(
            ModelProvider.tenant_id == tenant_id,
            ModelProvider.user_id == user_id,
            ModelProvider.is_default.is_(True),
            ModelProvider.enabled.is_(True),
        )
    )


async def resolve_for_session(
    db: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID | None
) -> tuple[ModelProvider, str | None] | None:
    """Resolve the effective (provider, model) for a run: session override → global
    default → None (caller then falls back to env ``PROVIDER_*``). Used by
    ``build_provider`` (MP.3)."""
    session: SessionModel | None = None
    if session_id is not None:
        session = await db.get(SessionModel, (tenant_id, session_id))
    if session is not None and session.model_provider_id is not None:
        p = await db.get(ModelProvider, (tenant_id, session.model_provider_id))
        if p is not None and p.enabled:
            return p, (session.model or p.default_model)
    if session is not None:
        p = await default_provider(db, tenant_id, session.user_id)
        if p is not None:
            return p, p.default_model
    return None


async def provider_for_session(
    db: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID | None
) -> Provider:
    """Build the streaming provider for a run from the owner's DB config (session override
    → global default), falling back to the env ``PROVIDER_*`` provider (offline/mock) when
    no source is configured or its key cannot be opened. The key is decrypted at this
    boundary only and never logged."""
    sel = await resolve_for_session(db, tenant_id=tenant_id, session_id=session_id)
    if sel is None:
        return build_provider()
    p, model = sel
    mdl = model or p.default_model
    if not mdl:
        return build_provider()
    try:
        key = open_key(p)
    except Exception:  # noqa: BLE001 - a broken key falls back to env, never crashes a run
        return build_provider()
    if key is None:
        return build_provider()
    return build_from_config(kind=p.kind, api_key=key, model=mdl, base_url=p.base_url)


# --- test connection (fetch the model catalog) ------------------------------


def _models_endpoint(kind: str, base_url: str | None) -> tuple[str, dict[str, str]]:
    """(url, headers) for listing models. The key header is filled by the caller."""
    if kind == "anthropic":
        base = (base_url or _ANTHROPIC_BASE).rstrip("/")
        return f"{base}/v1/models", {"anthropic-version": "2023-06-01"}
    if kind == "gemini":
        base = (base_url or _GEMINI_BASE).rstrip("/")
        return f"{base}/v1beta/models", {}
    base = (base_url or settings.provider_base_url).rstrip("/")
    return openai_endpoint(base, "models"), {}


def _parse_models(kind: str, data: object) -> list[str]:
    ids: list[str] = []
    if kind == "gemini":
        items = data.get("models") if isinstance(data, dict) else None
        for m in items or []:
            name = m.get("name") if isinstance(m, dict) else None
            if isinstance(name, str):
                ids.append(name.split("/")[-1])  # "models/gemini-x" → "gemini-x"
    else:
        items = data.get("data") if isinstance(data, dict) else None
        for m in items or []:
            mid = m.get("id") if isinstance(m, dict) else None
            if isinstance(mid, str):
                ids.append(mid)
    return ids[:200]


async def test_connection(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    provider_id: uuid.UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelProvider:
    """Decrypt the key server-side, fetch the source's model catalog, and persist the
    outcome (active + models / error + redacted reason). The key never leaves this boundary.
    Caller commits."""
    p = await get_provider(db, ctx, provider_id=provider_id)
    key = open_key(p)
    if key is None:
        return await record_test_result(
            db, ctx, provider_id=p.id, ok=False, error_redacted="no key set"
        )
    url, extra = _models_endpoint(p.kind, p.base_url)
    headers = dict(extra)
    if p.kind == "anthropic":
        headers["x-api-key"] = key
    elif p.kind == "gemini":
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(
            timeout=float(settings.provider_timeout_seconds), transport=transport
        ) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            detail = _redacted_body(resp)
            return await record_test_result(
                db,
                ctx,
                provider_id=p.id,
                ok=False,
                error_redacted=f"status={resp.status_code} {detail}",
            )
        models = _parse_models(p.kind, resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        return await record_test_result(
            db, ctx, provider_id=p.id, ok=False, error_redacted=type(exc).__name__
        )
    return await record_test_result(db, ctx, provider_id=p.id, ok=True, models=models)


def _redacted_body(resp: httpx.Response, *, limit: int = 300) -> str:
    import json

    try:
        return json.dumps(redact(resp.json()), separators=(",", ":"), ensure_ascii=False)[:limit]
    except Exception:  # noqa: BLE001
        return (resp.text or "")[:limit]
