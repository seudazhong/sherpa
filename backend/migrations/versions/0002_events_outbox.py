"""event journal + transactional outbox

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20

Raw DDL from docs/contracts/data-model.md. The journal is the append-only
source of truth (ADR-016); the outbox drives at-least-once relay to Redis Streams.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE event_journal (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            session_id uuid,
            session_seq bigint,
            run_id uuid NOT NULL,
            run_seq bigint NOT NULL,
            event_type text NOT NULL,
            envelope_version smallint NOT NULL,
            durability text NOT NULL DEFAULT 'durable',
            correlation_id uuid,
            causation_event_id uuid,
            payload_redacted jsonb NOT NULL,
            payload_size_bytes integer NOT NULL,
            occurred_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_event_journal PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_event_journal_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_event_journal_run FOREIGN KEY (tenant_id, run_id)
                REFERENCES runs (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_event_journal_run_session FOREIGN KEY (tenant_id, run_id, session_id)
                REFERENCES runs (tenant_id, id, session_id) ON DELETE RESTRICT,
            CONSTRAINT fk_event_journal_causation FOREIGN KEY (tenant_id, causation_event_id)
                REFERENCES event_journal (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_event_journal_run_seq UNIQUE (tenant_id, run_id, run_seq),
            CONSTRAINT ck_event_journal_sequences CHECK (
                run_seq > 0
                AND (
                    (session_id IS NULL AND session_seq IS NULL)
                    OR (session_id IS NOT NULL AND session_seq IS NOT NULL AND session_seq > 0)
                )
            ),
            CONSTRAINT ck_event_journal_type CHECK (char_length(event_type) BETWEEN 1 AND 200),
            CONSTRAINT ck_event_journal_envelope CHECK (envelope_version > 0),
            CONSTRAINT ck_event_journal_durability
                CHECK (durability IN ('durable', 'presentation')),
            CONSTRAINT ck_event_journal_payload CHECK (
                payload_size_bytes = octet_length(payload_redacted::text)
                AND payload_size_bytes BETWEEN 2 AND 65536
            )
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_event_journal_session_seq
            ON event_journal (tenant_id, session_id, session_seq)
            WHERE session_id IS NOT NULL;
    """)
    op.execute("""
        CREATE INDEX ix_event_journal_tenant_type_created
            ON event_journal (tenant_id, event_type, created_at DESC);
    """)
    op.execute("""
        CREATE INDEX ix_event_journal_tenant_correlation
            ON event_journal (tenant_id, correlation_id)
            WHERE correlation_id IS NOT NULL;
    """)

    op.execute("""
        CREATE TABLE outbox (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            event_id uuid NOT NULL,
            topic text NOT NULL,
            delivery_key text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            attempts integer NOT NULL DEFAULT 0,
            available_at timestamptz NOT NULL DEFAULT now(),
            locked_by text,
            locked_at timestamptz,
            delivered_at timestamptz,
            last_error_redacted text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_outbox PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_outbox_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_outbox_event FOREIGN KEY (tenant_id, event_id)
                REFERENCES event_journal (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_outbox_event_topic UNIQUE (tenant_id, event_id, topic),
            CONSTRAINT uq_outbox_delivery_key UNIQUE (tenant_id, topic, delivery_key),
            CONSTRAINT ck_outbox_status
                CHECK (status IN ('pending', 'publishing', 'delivered', 'failed')),
            CONSTRAINT ck_outbox_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_outbox_delivery CHECK (
                (status = 'delivered' AND delivered_at IS NOT NULL)
                OR (status <> 'delivered' AND delivered_at IS NULL)
            ),
            CONSTRAINT ck_outbox_error_bound CHECK (
                last_error_redacted IS NULL OR octet_length(last_error_redacted) <= 16384
            )
        );
    """)
    op.execute("""
        CREATE INDEX ix_outbox_ready
            ON outbox (available_at, created_at)
            WHERE status IN ('pending', 'publishing');
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outbox CASCADE;")
    op.execute("DROP TABLE IF EXISTS event_journal CASCADE;")
