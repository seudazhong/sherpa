"""audit_receipts (activity ledger, ADR-021)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md: the semantic "what Sherpa did on my behalf"
ledger (reads / inferences / actions), append-only at the application layer.

Deferred: the global append-only triggers (messages/parts/event_journal/
audit_receipts) from the data-model COMMIT block are intentionally NOT installed
here — they would block the tenant-cascade cleanup used across the test suite and
the whole-tenant erasure path. Append-only is enforced in the application until a
dedicated hardening task wires `app.allow_immutable_mutation` into every deleter.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_receipts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            receipt_version smallint NOT NULL,
            receipt_type text NOT NULL,
            actor_type text NOT NULL,
            actor_user_id uuid,
            trigger_type text NOT NULL,
            run_id uuid,
            invocation_id uuid,
            approval_envelope_id uuid,
            subject_type text,
            subject_id uuid,
            action text NOT NULL,
            outcome text NOT NULL,
            reversible boolean NOT NULL DEFAULT false,
            summary_redacted jsonb NOT NULL,
            source_event_id uuid,
            occurred_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_audit_receipts PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_audit_receipts_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_audit_receipts_actor
                FOREIGN KEY (tenant_id, actor_user_id)
                REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_audit_receipts_run
                FOREIGN KEY (tenant_id, run_id) REFERENCES runs (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_audit_receipts_invocation
                FOREIGN KEY (tenant_id, invocation_id)
                REFERENCES effect_invocations (tenant_id, invocation_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_audit_receipts_approval
                FOREIGN KEY (tenant_id, approval_envelope_id)
                REFERENCES approval_envelopes (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_audit_receipts_source_event
                FOREIGN KEY (tenant_id, source_event_id)
                REFERENCES event_journal (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_audit_receipts_version
                CHECK (receipt_version > 0),
            CONSTRAINT ck_audit_receipts_type
                CHECK (char_length(receipt_type) BETWEEN 1 AND 200),
            CONSTRAINT ck_audit_receipts_actor_type
                CHECK (actor_type IN ('user', 'system', 'connector', 'scheduler')),
            CONSTRAINT ck_audit_receipts_actor
                CHECK (
                    (actor_type = 'user' AND actor_user_id IS NOT NULL)
                    OR (actor_type <> 'user' AND actor_user_id IS NULL)
                ),
            CONSTRAINT ck_audit_receipts_subject
                CHECK (
                    (subject_type IS NULL AND subject_id IS NULL)
                    OR (subject_type IS NOT NULL AND subject_id IS NOT NULL)
                ),
            CONSTRAINT ck_audit_receipts_summary
                CHECK (octet_length(summary_redacted::text) <= 32768),
            CONSTRAINT ck_audit_receipts_text_bounds
                CHECK (
                    char_length(trigger_type) BETWEEN 1 AND 100
                    AND char_length(action) BETWEEN 1 AND 200
                    AND char_length(outcome) BETWEEN 1 AND 100
                    AND (subject_type IS NULL OR char_length(subject_type) BETWEEN 1 AND 100)
                )
        );
    """)
    op.execute("""
        CREATE INDEX ix_audit_receipts_tenant_occurred
            ON audit_receipts (tenant_id, occurred_at DESC);
    """)
    op.execute("""
        CREATE INDEX ix_audit_receipts_tenant_type_occurred
            ON audit_receipts (tenant_id, receipt_type, occurred_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_receipts CASCADE;")
