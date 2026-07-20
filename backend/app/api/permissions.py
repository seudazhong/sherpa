"""Permission resolution endpoints (api.md §4.7, §6) — FROZEN contract (ADR-020).

`GET /permissions` is a read projection of pending envelopes for the web inbox
(never exposes the single-use nonce). `POST /permissions/{id}/resolve` performs the
first-valid-response-wins transition: the submitted envelope must match every stored
immutable field (nonce, args hash, bindings, policy) and the actor must equal the
authorized decider. `{id}` is the approval `correlation_id`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ApprovalActor,
    ApprovalDecision,
    ApprovalPreview,
    ApprovalResolution,
    PendingApproval,
    PendingApprovalPage,
)
from app.api.schemas import (
    ApprovalEnvelope as ApprovalEnvelopeSchema,
)
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import ApprovalEnvelope
from app.permissions import (
    ApprovalBindingMismatch,
    ApprovalExpired,
    ResolveError,
    nonce_hash,
    resolve_approval,
)

router = APIRouter(tags=["permissions"])


@router.get("/permissions")
async def list_permissions(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PendingApprovalPage:
    rows = (
        (
            await db.execute(
                select(ApprovalEnvelope)
                .where(
                    ApprovalEnvelope.tenant_id == ctx.tenant_id,
                    ApprovalEnvelope.status == "pending",
                )
                .order_by(ApprovalEnvelope.requested_at.desc(), ApprovalEnvelope.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return PendingApprovalPage(
        items=[
            PendingApproval(
                correlation_id=r.correlation_id,
                tenant_id=r.tenant_id,
                run_id=r.run_id,
                session_id=r.session_id,
                invocation_id=r.invocation_id,
                tool_name=r.tool_name,
                permission_scope=r.permission_scope,
                effect_class=r.effect_class,  # type: ignore[arg-type]
                policy_version=r.policy_version,
                normalized_args_hash=r.args_hash.hex(),
                human_readable_preview=ApprovalPreview.model_validate(r.preview_redacted),
                authorized_actor=ApprovalActor(type="user", id=r.authorized_decider_user_id),
                expires_at=r.expires_at,
                requested_at=r.requested_at,
            )
            for r in rows
        ],
        next_cursor=None,
    )


@router.post("/permissions/{correlation_id}/resolve")
async def resolve_permission(
    correlation_id: uuid.UUID,
    body: ApprovalEnvelopeSchema,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalResolution:
    if body.correlation_id != correlation_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "correlation_mismatch")
    if body.decision is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "decision_required")
    if body.decision.channel != "web":
        # Contract reserves qq/email for a future renderer/security review.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported_channel")
    if body.bound.tenant_id != ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "approval_actor_mismatch")
    if body.decision.actor.id != ctx.user_id or body.authorized_actor.id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "approval_actor_mismatch")

    def verify(row: ApprovalEnvelope) -> None:
        if (
            body.bound.tenant_id != row.tenant_id
            or body.bound.run_id != row.run_id
            or body.bound.invocation_id != row.invocation_id
            or body.action.tool_name != row.tool_name
            or body.action.permission_scope != row.permission_scope
            or body.action.session_id != row.session_id
            or body.effect_class != row.effect_class
            or body.normalized_args_hash != row.args_hash.hex()
            or nonce_hash(body.nonce) != row.nonce_hash
            or body.policy_version != row.policy_version
            or body.authorized_actor.id != row.authorized_decider_user_id
        ):
            raise ApprovalBindingMismatch

    try:
        result = await resolve_approval(
            db,
            tenant_id=ctx.tenant_id,
            correlation_id=correlation_id,
            actor_id=ctx.user_id,
            channel=body.decision.channel,
            choice=body.decision.choice,
            verify=verify,
        )
    except ApprovalExpired as exc:
        await db.commit()  # persist the terminal pending -> expired transition
        raise HTTPException(exc.status_code, exc.detail) from None
    except ResolveError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None

    row = result.envelope
    await db.commit()
    return ApprovalResolution(
        correlation_id=row.correlation_id,
        state="resolved",
        winning_decision=ApprovalDecision(
            actor=ApprovalActor(type="user", id=row.decided_by_user_id),  # type: ignore[arg-type]
            channel=row.decided_via_channel,  # type: ignore[arg-type]
            choice=row.decision,  # type: ignore[arg-type]
        ),
        decided_at=row.decided_at,  # type: ignore[arg-type]
    )
