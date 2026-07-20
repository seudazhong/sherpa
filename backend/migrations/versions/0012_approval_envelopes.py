"""approval_envelopes (permission engine, ADR-020)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md. A pending envelope binds a suspended run +
persisted effect invocation awaiting permission; resolution is first-valid-wins
(pending -> decided). All hashes are 32 bytes; the preview is redacted plain text.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE approval_envelopes (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            envelope_version smallint NOT NULL,
            correlation_id uuid NOT NULL,
            run_id uuid NOT NULL,
            session_id uuid NOT NULL,
            invocation_id uuid NOT NULL,
            tool_name text NOT NULL,
            permission_scope text NOT NULL,
            effect_class text NOT NULL,
            args_hash bytea NOT NULL,
            policy_version text NOT NULL,
            expires_at timestamptz NOT NULL,
            nonce_hash bytea NOT NULL,
            preview_redacted jsonb NOT NULL,
            authorized_decider_user_id uuid NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            decision text,
            decided_by_user_id uuid,
            decided_via_channel text,
            decided_at timestamptz,
            version integer NOT NULL DEFAULT 1,
            requested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_approval_envelopes PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_approval_envelopes_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_approval_envelopes_run_session
                FOREIGN KEY (tenant_id, run_id, session_id)
                REFERENCES runs (tenant_id, id, session_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_approval_envelopes_invocation_class
                FOREIGN KEY (tenant_id, invocation_id, effect_class)
                REFERENCES effect_invocations (
                    tenant_id,
                    invocation_id,
                    effect_class
                )
                ON DELETE RESTRICT,
            CONSTRAINT fk_approval_envelopes_authorized_decider
                FOREIGN KEY (tenant_id, authorized_decider_user_id)
                REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_approval_envelopes_decider
                FOREIGN KEY (tenant_id, decided_by_user_id)
                REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_approval_envelopes_correlation
                UNIQUE (tenant_id, correlation_id),
            CONSTRAINT uq_approval_envelopes_nonce
                UNIQUE (tenant_id, nonce_hash),
            CONSTRAINT ck_approval_envelopes_version
                CHECK (envelope_version > 0 AND version > 0),
            CONSTRAINT ck_approval_envelopes_hashes
                CHECK (
                    octet_length(args_hash) = 32
                    AND octet_length(nonce_hash) = 32
                ),
            CONSTRAINT ck_approval_envelopes_action
                CHECK (
                    char_length(tool_name) BETWEEN 1 AND 200
                    AND char_length(permission_scope) BETWEEN 1 AND 512
                    AND effect_class IN (
                        'read_only',
                        'idempotent_write',
                        'reconcilable_write',
                        'non_idempotent_write'
                    )
                ),
            CONSTRAINT ck_approval_envelopes_preview
                CHECK (octet_length(preview_redacted::text) <= 32768),
            CONSTRAINT ck_approval_envelopes_status
                CHECK (status IN ('pending', 'decided', 'expired', 'superseded')),
            CONSTRAINT ck_approval_envelopes_decision
                CHECK (
                    decision IS NULL
                    OR decision IN (
                        'allow_once',
                        'allow_session',
                        'always',
                        'reject'
                    )
                ),
            CONSTRAINT ck_approval_envelopes_state
                CHECK (
                    (
                        status = 'pending'
                        AND decision IS NULL
                        AND decided_by_user_id IS NULL
                        AND decided_via_channel IS NULL
                        AND decided_at IS NULL
                    )
                    OR (
                        status = 'decided'
                        AND decision IS NOT NULL
                        AND decided_by_user_id IS NOT NULL
                        AND decided_via_channel IS NOT NULL
                        AND decided_at IS NOT NULL
                    )
                    OR (
                        status IN ('expired', 'superseded')
                        AND decision IS NULL
                        AND decided_by_user_id IS NULL
                        AND decided_via_channel IS NULL
                        AND decided_at IS NULL
                    )
                ),
            CONSTRAINT ck_approval_envelopes_expiry
                CHECK (expires_at > requested_at)
        );
    """)
    op.execute("""
        CREATE INDEX ix_approval_envelopes_pending_expiry
            ON approval_envelopes (expires_at, tenant_id, id)
            WHERE status = 'pending';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_envelopes CASCADE;")
