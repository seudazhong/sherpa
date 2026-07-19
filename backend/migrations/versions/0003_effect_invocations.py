"""effect invocations (persist-before-effect + idempotency)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md. Every side effect persists an invocation
before dispatch (ADR-017); outcomes are succeeded|failed|effect_unknown.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE effect_invocations (
            tenant_id uuid NOT NULL,
            invocation_id uuid NOT NULL,
            run_id uuid NOT NULL,
            turn_seq bigint,
            effect_name text NOT NULL,
            idempotency_key text NOT NULL,
            effect_class text NOT NULL,
            retry_policy text NOT NULL,
            args_hash bytea NOT NULL,
            status text NOT NULL DEFAULT 'prepared',
            outcome text,
            attempts integer NOT NULL DEFAULT 0,
            reconciliation_state text NOT NULL DEFAULT 'not_required',
            result_redacted jsonb,
            external_reference_redacted text,
            last_error_redacted text,
            started_at timestamptz,
            settled_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_effect_invocations PRIMARY KEY (tenant_id, invocation_id),
            CONSTRAINT uq_effect_invocations_class_binding
                UNIQUE (tenant_id, invocation_id, effect_class),
            CONSTRAINT fk_effect_invocations_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_effect_invocations_run FOREIGN KEY (tenant_id, run_id)
                REFERENCES runs (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_effect_invocations_idempotency UNIQUE (tenant_id, idempotency_key),
            CONSTRAINT ck_effect_invocations_turn CHECK (turn_seq IS NULL OR turn_seq > 0),
            CONSTRAINT ck_effect_invocations_name CHECK (char_length(effect_name) BETWEEN 1 AND 200),
            CONSTRAINT ck_effect_invocations_key
                CHECK (char_length(idempotency_key) BETWEEN 1 AND 512),
            CONSTRAINT ck_effect_invocations_class CHECK (
                effect_class IN ('read_only', 'idempotent_write', 'reconcilable_write',
                                 'non_idempotent_write')
            ),
            CONSTRAINT ck_effect_invocations_retry_policy CHECK (
                retry_policy IN ('transient_before_dispatch', 'same_key', 'after_reconcile', 'never')
            ),
            CONSTRAINT ck_effect_invocations_hash CHECK (octet_length(args_hash) = 32),
            CONSTRAINT ck_effect_invocations_status CHECK (
                status IN ('prepared', 'running', 'settled', 'needs_reconciliation')
            ),
            CONSTRAINT ck_effect_invocations_outcome CHECK (
                outcome IS NULL OR outcome IN ('succeeded', 'failed', 'effect_unknown')
            ),
            CONSTRAINT ck_effect_invocations_reconciliation CHECK (
                reconciliation_state IN ('not_required', 'pending', 'manual_required',
                                         'resolved_succeeded', 'resolved_failed')
            ),
            CONSTRAINT ck_effect_invocations_state CHECK (
                (status IN ('prepared', 'running') AND outcome IS NULL AND settled_at IS NULL)
                OR (status = 'settled' AND outcome IN ('succeeded', 'failed')
                    AND settled_at IS NOT NULL)
                OR (status = 'needs_reconciliation' AND outcome = 'effect_unknown'
                    AND reconciliation_state IN ('pending', 'manual_required')
                    AND settled_at IS NOT NULL)
            ),
            CONSTRAINT ck_effect_invocations_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_effect_invocations_result_bound CHECK (
                result_redacted IS NULL OR octet_length(result_redacted::text) <= 65536
            ),
            CONSTRAINT ck_effect_invocations_external_ref_bound CHECK (
                external_reference_redacted IS NULL
                OR octet_length(external_reference_redacted) <= 2048
            ),
            CONSTRAINT ck_effect_invocations_error_bound CHECK (
                last_error_redacted IS NULL OR octet_length(last_error_redacted) <= 16384
            )
        );
    """)
    op.execute("""
        CREATE INDEX ix_effect_invocations_unresolved
            ON effect_invocations (tenant_id, status, created_at)
            WHERE status IN ('prepared', 'running', 'needs_reconciliation');
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS effect_invocations CASCADE;")
