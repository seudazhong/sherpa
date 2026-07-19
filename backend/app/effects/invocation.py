"""Persist-before-effect + idempotency helpers (ADR-017, events-and-effects §4).

Every side effect first commits an invocation (`prepared`) keyed by a deterministic
idempotency key. Retries/turn-recovery reuse the same key, so a duplicate begin is a
no-op that returns the existing invocation. Outcomes are succeeded|failed|effect_unknown;
`effect_unknown` moves to needs_reconciliation and must NOT be blindly retried.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def args_hash(args: dict[str, object]) -> bytes:
    """Deterministic 32-byte SHA-256 over canonical args JSON."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).digest()


@dataclasses.dataclass(frozen=True)
class InvocationHandle:
    invocation_id: uuid.UUID
    status: str
    created: bool


_INSERT = text("""
    INSERT INTO effect_invocations (
        tenant_id, invocation_id, run_id, turn_seq, effect_name,
        idempotency_key, effect_class, retry_policy, args_hash
    ) VALUES (
        :tenant_id, :invocation_id, :run_id, :turn_seq, :effect_name,
        :idempotency_key, :effect_class, :retry_policy, :args_hash
    )
    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
    RETURNING invocation_id
""")

_SELECT_EXISTING = text("""
    SELECT invocation_id, status FROM effect_invocations
    WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key
""")


async def begin_invocation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    effect_name: str,
    idempotency_key: str,
    effect_class: str,
    retry_policy: str,
    args: dict[str, object],
    turn_seq: int | None = None,
) -> InvocationHandle:
    """Persist a `prepared` invocation, or return the existing one for this key."""
    new_id = uuid.uuid4()
    inserted = (
        await session.execute(
            _INSERT,
            {
                "tenant_id": tenant_id,
                "invocation_id": new_id,
                "run_id": run_id,
                "turn_seq": turn_seq,
                "effect_name": effect_name,
                "idempotency_key": idempotency_key,
                "effect_class": effect_class,
                "retry_policy": retry_policy,
                "args_hash": args_hash(args),
            },
        )
    ).first()
    if inserted is not None:
        return InvocationHandle(new_id, "prepared", created=True)
    existing = (
        await session.execute(
            _SELECT_EXISTING, {"tenant_id": tenant_id, "idempotency_key": idempotency_key}
        )
    ).one()
    return InvocationHandle(existing.invocation_id, existing.status, created=False)


async def mark_running(
    session: AsyncSession, tenant_id: uuid.UUID, invocation_id: uuid.UUID
) -> None:
    await session.execute(
        text("""
            UPDATE effect_invocations
            SET status = 'running', started_at = COALESCE(started_at, now()),
                attempts = attempts + 1, updated_at = now()
            WHERE tenant_id = :tenant_id AND invocation_id = :invocation_id
              AND status IN ('prepared', 'running')
        """),
        {"tenant_id": tenant_id, "invocation_id": invocation_id},
    )


async def _settle(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invocation_id: uuid.UUID,
    *,
    status: str,
    outcome: str,
    reconciliation_state: str | None = None,
    result: dict[str, object] | None = None,
    external_reference: str | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        text("""
            UPDATE effect_invocations
            SET status = :status, outcome = :outcome, settled_at = now(),
                reconciliation_state = COALESCE(:reconciliation_state, reconciliation_state),
                result_redacted = CAST(:result AS jsonb),
                external_reference_redacted = :external_reference,
                last_error_redacted = :error, updated_at = now()
            WHERE tenant_id = :tenant_id AND invocation_id = :invocation_id
        """),
        {
            "tenant_id": tenant_id,
            "invocation_id": invocation_id,
            "status": status,
            "outcome": outcome,
            "reconciliation_state": reconciliation_state,
            "result": json.dumps(result, separators=(",", ":")) if result is not None else None,
            "external_reference": external_reference,
            "error": error,
        },
    )


async def settle_succeeded(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invocation_id: uuid.UUID,
    *,
    result: dict[str, object] | None = None,
    external_reference: str | None = None,
) -> None:
    await _settle(
        session,
        tenant_id,
        invocation_id,
        status="settled",
        outcome="succeeded",
        result=result,
        external_reference=external_reference,
    )


async def settle_failed(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invocation_id: uuid.UUID,
    *,
    error: str | None = None,
) -> None:
    await _settle(
        session, tenant_id, invocation_id, status="settled", outcome="failed", error=error
    )


async def settle_unknown(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invocation_id: uuid.UUID,
    *,
    error: str | None = None,
) -> None:
    """effect_unknown -> stop and require reconciliation; never blindly retry."""
    await _settle(
        session,
        tenant_id,
        invocation_id,
        status="needs_reconciliation",
        outcome="effect_unknown",
        reconciliation_state="pending",
        error=error,
    )
