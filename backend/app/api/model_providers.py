"""Model providers REST surface (api.md §10.8; ADR-041).

Thin adapter over ``app.services.model_providers`` so the Settings **Models** panel can
configure multiple model sources, test connections, pick a global default, and set a
per-conversation model. The API key is **write-only** (AEAD-sealed server-side, never
returned). Reads need a session; writes also need CSRF. There is **no agent tool** —
provider configuration crosses the credential boundary and is an owner Settings action.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import ModelProvider
from app.services import CallerContext, ServiceError
from app.services import model_providers as svc

router = APIRouter(tags=["model-providers"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class ModelProviderSummary(BaseModel):
    id: uuid.UUID
    kind: Literal["openai_compatible", "anthropic", "gemini"]
    display_name: str
    base_url: str | None
    models: list[str]
    default_model: str | None
    enabled: bool
    is_default: bool
    status: Literal["pending", "active", "error"]
    last_error: str | None
    has_key: bool
    updated_at: datetime.datetime


class ModelProviderCreate(BaseModel):
    kind: Literal["openai_compatible", "anthropic", "gemini"]
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    base_url: str | None = None
    api_key: Annotated[str, Field(min_length=1, max_length=8000)]
    default_model: str | None = None


class ModelProviderUpdate(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    enabled: bool | None = None


class ModelProviderTest(BaseModel):
    ok: bool
    status: Literal["active", "error"]
    models: list[str]
    detail: str | None


class SessionModelSelection(BaseModel):
    model_provider_id: uuid.UUID | None = None
    model: str | None = None


class SessionModelState(SessionModelSelection):
    """Selection + the model a run on this session would actually use (ADR-041).

    Clients must render `effective_*`: once a source is configured, the env
    `PROVIDER_MODEL` echoed by `GET /meta` is no longer the truth (backlog B-1)."""

    effective_source: Literal["session", "default", "env"]
    effective_provider_id: uuid.UUID | None
    effective_provider_name: str | None
    effective_kind: str
    effective_model: str


def _summary(p: ModelProvider) -> ModelProviderSummary:
    return ModelProviderSummary(
        id=p.id,
        kind=p.kind,  # type: ignore[arg-type]
        display_name=p.display_name,
        base_url=p.base_url,
        models=list(p.models or []),
        default_model=p.default_model,
        enabled=p.enabled,
        is_default=p.is_default,
        status=p.status,  # type: ignore[arg-type]
        last_error=p.last_error_redacted,
        has_key=p.token_enc is not None,
        updated_at=p.updated_at,
    )


def _state(s: svc.SessionModelState) -> SessionModelState:
    return SessionModelState(
        model_provider_id=s.model_provider_id,
        model=s.model,
        effective_source=s.effective_source,  # type: ignore[arg-type]
        effective_provider_id=s.effective_provider_id,
        effective_provider_name=s.effective_provider_name,
        effective_kind=s.effective_kind,
        effective_model=s.effective_model,
    )


@router.get("/providers")
async def list_providers(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ModelProviderSummary]:
    rows = await svc.list_providers(db, _caller(ctx))
    return [_summary(p) for p in rows]


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ModelProviderCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ModelProviderSummary:
    try:
        p = await svc.create_provider(
            db,
            _caller(ctx),
            kind=body.kind,
            display_name=body.display_name,
            api_key=body.api_key,
            base_url=body.base_url,
            default_model=body.default_model,
        )
        out = _summary(p)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return out


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ModelProviderSummary:
    try:
        p = await svc.get_provider(db, _caller(ctx), provider_id=provider_id)
    except ServiceError as e:
        raise _http(e) from None
    return _summary(p)


@router.patch("/providers/{provider_id}")
async def update_provider(
    provider_id: uuid.UUID,
    body: ModelProviderUpdate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ModelProviderSummary:
    try:
        p = await svc.update_provider(
            db,
            _caller(ctx),
            provider_id=provider_id,
            display_name=body.display_name,
            base_url=body.base_url,
            api_key=body.api_key,
            default_model=body.default_model,
            enabled=body.enabled,
        )
        out = _summary(p)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return out


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    try:
        await svc.delete_provider(db, _caller(ctx), provider_id=provider_id)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ModelProviderTest:
    try:
        p = await svc.test_connection(db, _caller(ctx), provider_id=provider_id)
        out = ModelProviderTest(
            ok=p.status == "active",
            status="active" if p.status == "active" else "error",
            models=list(p.models or []),
            detail=p.last_error_redacted,
        )
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return out


@router.get("/providers/{provider_id}/models")
async def list_provider_models(
    provider_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    try:
        p = await svc.get_provider(db, _caller(ctx), provider_id=provider_id)
    except ServiceError as e:
        raise _http(e) from None
    return list(p.models or [])


@router.post("/providers/{provider_id}/default")
async def set_default_provider(
    provider_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ModelProviderSummary:
    try:
        p = await svc.set_default(db, _caller(ctx), provider_id=provider_id)
        out = _summary(p)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return out


@router.get("/sessions/{session_id}/model")
async def get_session_model(
    session_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SessionModelState:
    try:
        state = await svc.get_session_model_state(db, _caller(ctx), session_id=session_id)
    except ServiceError as e:
        raise _http(e) from None
    return _state(state)


@router.post("/sessions/{session_id}/model")
async def set_session_model(
    session_id: uuid.UUID,
    body: SessionModelSelection,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SessionModelState:
    try:
        await svc.set_session_model(
            db,
            _caller(ctx),
            session_id=session_id,
            model_provider_id=body.model_provider_id,
            model=body.model,
        )
        state = await svc.get_session_model_state(db, _caller(ctx), session_id=session_id)
        await db.commit()
    except ServiceError as e:
        await db.rollback()
        raise _http(e) from None
    return _state(state)
