"""traces (operational telemetry projected from the event stream)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md (traces). `generations` and
`audit_receipts` are intentionally deferred to M2: they FK to `extractions` and
`approval_envelopes` respectively (M2 tables), and audit_receipts additionally
carries the append-only immutability trigger. v1 projects per-run model/token/
cost onto trace.tags and the sessions.* rollups.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE traces (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            parent_trace_id uuid,
            run_id uuid,
            session_id uuid,
            user_id uuid,
            trace_kind text NOT NULL,
            status text NOT NULL DEFAULT 'running',
            tags jsonb NOT NULL DEFAULT '{}'::jsonb,
            started_at timestamptz NOT NULL DEFAULT now(),
            ended_at timestamptz,
            CONSTRAINT pk_traces PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_traces_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_traces_parent
                FOREIGN KEY (tenant_id, parent_trace_id) REFERENCES traces (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_traces_run
                FOREIGN KEY (tenant_id, run_id) REFERENCES runs (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_traces_session
                FOREIGN KEY (tenant_id, session_id) REFERENCES sessions (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_traces_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_traces_kind
                CHECK (
                    trace_kind IN (
                        'web_chat', 'gmail_sync', 'candidate_extraction', 'schedule_delivery'
                    )
                ),
            CONSTRAINT ck_traces_status
                CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
            CONSTRAINT ck_traces_tags_bound
                CHECK (octet_length(tags::text) <= 16384),
            CONSTRAINT ck_traces_ended
                CHECK (
                    (status = 'running' AND ended_at IS NULL)
                    OR (status <> 'running' AND ended_at IS NOT NULL)
                )
        );
    """)
    op.execute("""
        CREATE INDEX ix_traces_tenant_run
            ON traces (tenant_id, run_id)
            WHERE run_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS traces CASCADE;")
