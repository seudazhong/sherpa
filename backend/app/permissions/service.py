"""Permission engine service (api.md §6, ADR-020).

Two operations:

* ``request_approval`` — persist a *pending* envelope bound to a suspended run and
  its already-persisted effect invocation. The raw single-use ``nonce`` is returned
  once (only its SHA-256 is stored) so the asking channel can carry it to the
  decider; it is never re-derivable from storage.
* ``resolve_approval`` — the first-valid-response-wins transition. The row is locked
  ``FOR UPDATE`` so concurrent resolves serialize: the first satisfying submission
  flips ``pending -> decided`` and wins; an exact retry of the winner is idempotent;
  any different later submission returns *already resolved*; expiry is terminal.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import secrets
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.effects import args_hash
from app.models import ApprovalEnvelope
from app.permissions import policy

_NS = uuid.NAMESPACE_URL
_VALID_CHOICES = ("allow_once", "allow_session", "always", "reject")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def correlation_for(invocation_id: uuid.UUID) -> uuid.UUID:
    """Deterministic correlation id per invocation so a retried gate reuses the ask."""
    return uuid.uuid5(_NS, f"sherpa:approval:{invocation_id}")


def nonce_hash(nonce: str) -> bytes:
    return hashlib.sha256(nonce.encode("utf-8")).digest()


def build_preview(tool_name: str, args: dict[str, object]) -> dict[str, object]:
    """Bounded, plain-text, redacted preview for human display (never markup)."""
    if tool_name in ("fs_write", "fs_edit", "fs_delete"):
        path = str(args.get("path") or "")[:1000]
        details: list[dict[str, str]] = [{"label": "path", "value": path}]
        if tool_name == "fs_delete":
            details.append({"label": "recursive", "value": str(bool(args.get("recursive", False)))})
        return {
            "action": tool_name,
            "summary": f"Approve {tool_name} for {path}"[:2000],
            "details": details,
            "risk": "Sensitive Project path; changes remain reviewable until Save.",
        }
    if tool_name == "sh_exec":
        command = str(args.get("command") or "")[:2000]
        return {
            "action": tool_name,
            "summary": f"Approve sandbox command: {command}"[:2000],
            "details": [
                {"label": "command", "value": command},
                {
                    "label": "runtime_session_id",
                    "value": str(args.get("runtime_session_id") or "")[:1000],
                },
            ],
            "risk": "Command executes inside the offline Project sandbox.",
        }
    details = [{"label": str(k)[:100], "value": str(v)[:1000]} for k, v in list(args.items())[:20]]
    return {
        "action": tool_name[:200],
        "summary": f"Approve {tool_name} with {len(details)} argument(s)"[:2000],
        "details": details,
        "risk": "External action; runs only after you approve." if details else None,
    }


@dataclasses.dataclass(frozen=True)
class CreatedApproval:
    envelope: ApprovalEnvelope
    nonce: str


async def request_approval(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    invocation_id: uuid.UUID,
    tool_name: str,
    effect_class: str,
    args: dict[str, object],
    decider_user_id: uuid.UUID,
    ttl_seconds: int = 3600,
    preview: dict[str, object] | None = None,
) -> CreatedApproval:
    """Create (or return the existing) pending envelope for a gated invocation."""
    correlation_id = correlation_for(invocation_id)
    existing = await session.scalar(
        select(ApprovalEnvelope).where(
            ApprovalEnvelope.tenant_id == tenant_id,
            ApprovalEnvelope.correlation_id == correlation_id,
        )
    )
    if existing is not None:
        return CreatedApproval(existing, nonce="")

    nonce = secrets.token_urlsafe(32)
    now = _now()
    envelope = ApprovalEnvelope(
        tenant_id=tenant_id,
        id=uuid.uuid4(),
        envelope_version=1,
        correlation_id=correlation_id,
        run_id=run_id,
        session_id=session_id,
        invocation_id=invocation_id,
        tool_name=tool_name,
        permission_scope=policy.permission_scope(tool_name, args),
        effect_class=effect_class,
        args_hash=args_hash(args),
        policy_version=policy.POLICY_VERSION,
        expires_at=now + datetime.timedelta(seconds=ttl_seconds),
        nonce_hash=nonce_hash(nonce),
        preview_redacted=preview or build_preview(tool_name, args),
        authorized_decider_user_id=decider_user_id,
        status="pending",
        version=1,
        requested_at=now,
    )
    session.add(envelope)
    await session.flush()
    return CreatedApproval(envelope, nonce=nonce)


class ResolveError(Exception):
    """Base for resolution failures, each carrying an HTTP status + stable detail."""

    status_code = 400
    detail = "approval_error"


class ApprovalNotFound(ResolveError):
    status_code = 404
    detail = "approval_not_found"


class ApprovalBindingMismatch(ResolveError):
    """Invalid nonce/binding/args — must NOT reveal the stored envelope."""

    status_code = 422
    detail = "approval_binding_mismatch"


class ApprovalActorMismatch(ResolveError):
    status_code = 403
    detail = "approval_actor_mismatch"


class ApprovalAlreadyResolved(ResolveError):
    status_code = 409
    detail = "approval_already_resolved"


class ApprovalExpired(ResolveError):
    status_code = 410
    detail = "approval_expired"


@dataclasses.dataclass(frozen=True)
class Resolution:
    envelope: ApprovalEnvelope
    idempotent: bool
    mutated: bool


async def resolve_approval(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
    actor_id: uuid.UUID,
    channel: str,
    choice: str,
    verify: Callable[[ApprovalEnvelope], None],
) -> Resolution:
    """First-valid-response-wins transition. Caller commits on success/expiry."""
    if choice not in _VALID_CHOICES:
        raise ApprovalBindingMismatch
    row = await session.scalar(
        select(ApprovalEnvelope)
        .where(
            ApprovalEnvelope.tenant_id == tenant_id,
            ApprovalEnvelope.correlation_id == correlation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApprovalNotFound

    # Immutable-field binding check (nonce/args/bound/action/policy). Failures are
    # generic and never echo stored data.
    verify(row)

    # Actor authorization: only the exact authorized decider may resolve.
    if actor_id != row.authorized_decider_user_id:
        raise ApprovalActorMismatch

    now = _now()
    if row.status == "pending" and now >= row.expires_at:
        row.status = "expired"
        await session.flush()
        raise ApprovalExpired
    if row.status in ("expired", "superseded"):
        raise ApprovalExpired if row.status == "expired" else ApprovalAlreadyResolved

    if row.status == "decided":
        if (
            row.decided_by_user_id == actor_id
            and row.decision == choice
            and row.decided_via_channel == channel
        ):
            return Resolution(row, idempotent=True, mutated=False)
        raise ApprovalAlreadyResolved

    # status == 'pending' and not expired: this submission wins.
    row.status = "decided"
    row.decision = choice
    row.decided_by_user_id = actor_id
    row.decided_via_channel = channel
    row.decided_at = now
    row.version += 1
    await session.flush()
    return Resolution(row, idempotent=False, mutated=True)
