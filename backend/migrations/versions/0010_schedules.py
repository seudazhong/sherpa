"""schedules + schedule_firings (scheduler)

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md. schedule_firings enforce
advance-cursor-then-run with a unique (schedule, scheduled_for) slot so a slot
fires at most once; delivery idempotency + reconciliation land with
notifications (M2 #19). approval_envelopes lands with M2 #20.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE schedules (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            todo_id uuid,
            kind text NOT NULL,
            name varchar(200) NOT NULL,
            reminder_kind text,
            delivery_channel text NOT NULL,
            timezone text NOT NULL,
            local_time time,
            next_fire_at timestamptz NOT NULL,
            last_fired_at timestamptz,
            misfire_policy text NOT NULL,
            duplicate_policy text NOT NULL,
            status text NOT NULL DEFAULT 'active',
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_schedules PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_schedules_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_schedules_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_schedules_todo
                FOREIGN KEY (tenant_id, todo_id) REFERENCES todos (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_schedules_kind CHECK (kind IN ('todo_reminder', 'daily_digest')),
            CONSTRAINT ck_schedules_name CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_schedules_reminder_kind
                CHECK (reminder_kind IS NULL OR reminder_kind IN ('due_soon', 'overdue')),
            CONSTRAINT ck_schedules_kind_target CHECK (
                (kind = 'todo_reminder' AND todo_id IS NOT NULL AND reminder_kind IS NOT NULL
                 AND local_time IS NULL)
                OR (kind = 'daily_digest' AND todo_id IS NULL AND reminder_kind IS NULL
                    AND local_time IS NOT NULL)
            ),
            CONSTRAINT ck_schedules_delivery_channel
                CHECK (delivery_channel IN ('web', 'digest_email')),
            CONSTRAINT ck_schedules_timezone CHECK (char_length(timezone) BETWEEN 1 AND 100),
            CONSTRAINT ck_schedules_misfire CHECK (misfire_policy IN ('skip', 'fire_once')),
            CONSTRAINT ck_schedules_duplicate
                CHECK (duplicate_policy IN ('prefer_no_duplicate', 'eventual_delivery')),
            CONSTRAINT ck_schedules_status
                CHECK (status IN ('active', 'paused', 'completed', 'disabled')),
            CONSTRAINT ck_schedules_version CHECK (version > 0)
        );
    """)
    op.execute(
        "CREATE INDEX ix_schedules_due ON schedules (next_fire_at, tenant_id, id) "
        "WHERE status = 'active';"
    )
    op.execute("""
        CREATE UNIQUE INDEX ux_schedules_active_todo_channel
            ON schedules (tenant_id, todo_id, reminder_kind, delivery_channel)
            WHERE kind = 'todo_reminder' AND status = 'active';
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_schedules_active_digest_channel
            ON schedules (tenant_id, user_id, delivery_channel)
            WHERE kind = 'daily_digest' AND status = 'active';
    """)

    op.execute("""
        CREATE TABLE schedule_firings (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            schedule_id uuid NOT NULL,
            firing_key text NOT NULL,
            scheduled_for timestamptz NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            delivery_outcome text,
            delivery_idempotency_key text NOT NULL,
            invocation_id uuid,
            attempts integer NOT NULL DEFAULT 0,
            available_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            settled_at timestamptz,
            last_error_redacted text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_schedule_firings PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_schedule_firings_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_schedule_firings_schedule
                FOREIGN KEY (tenant_id, schedule_id) REFERENCES schedules (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_schedule_firings_invocation
                FOREIGN KEY (tenant_id, invocation_id)
                REFERENCES effect_invocations (tenant_id, invocation_id) ON DELETE RESTRICT,
            CONSTRAINT uq_schedule_firings_key UNIQUE (tenant_id, schedule_id, firing_key),
            CONSTRAINT uq_schedule_firings_slot UNIQUE (tenant_id, schedule_id, scheduled_for),
            CONSTRAINT uq_schedule_firings_delivery_key
                UNIQUE (tenant_id, delivery_idempotency_key),
            CONSTRAINT uq_schedule_firings_invocation UNIQUE (tenant_id, invocation_id),
            CONSTRAINT ck_schedule_firings_key CHECK (
                char_length(firing_key) BETWEEN 1 AND 512
                AND char_length(delivery_idempotency_key) BETWEEN 1 AND 512
            ),
            CONSTRAINT ck_schedule_firings_status CHECK (status IN ('pending', 'running', 'settled')),
            CONSTRAINT ck_schedule_firings_outcome CHECK (
                delivery_outcome IS NULL
                OR delivery_outcome IN ('missed', 'failed', 'unknown', 'delivered')
            ),
            CONSTRAINT ck_schedule_firings_state CHECK (
                (status IN ('pending', 'running') AND delivery_outcome IS NULL
                 AND settled_at IS NULL)
                OR (status = 'settled' AND delivery_outcome IS NOT NULL AND settled_at IS NOT NULL)
            ),
            CONSTRAINT ck_schedule_firings_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_schedule_firings_error_bound
                CHECK (last_error_redacted IS NULL OR octet_length(last_error_redacted) <= 16384)
        );
    """)
    op.execute("""
        CREATE INDEX ix_schedule_firings_ready
            ON schedule_firings (available_at, tenant_id, id) WHERE status = 'pending';
    """)
    op.execute("""
        CREATE INDEX ix_schedule_firings_attention
            ON schedule_firings (tenant_id, delivery_outcome, settled_at DESC)
            WHERE delivery_outcome IN ('missed', 'failed', 'unknown');
    """)


def downgrade() -> None:
    for table in ("schedule_firings", "schedules"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
