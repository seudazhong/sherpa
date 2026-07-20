"""user_settings (notification preferences)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md. The web inbox is projected from
schedule_firings; this table holds the per-user notification preferences
(opt-in, quiet hours, daily cap, channels).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_settings (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            notifications_enabled boolean NOT NULL DEFAULT false,
            web_enabled boolean NOT NULL DEFAULT true,
            email_digest_enabled boolean NOT NULL DEFAULT false,
            timezone text NOT NULL DEFAULT 'UTC',
            digest_time time NOT NULL DEFAULT TIME '08:00:00',
            quiet_hours_enabled boolean NOT NULL DEFAULT true,
            quiet_hours_start time NOT NULL DEFAULT TIME '22:00:00',
            quiet_hours_end time NOT NULL DEFAULT TIME '08:00:00',
            daily_cap integer NOT NULL DEFAULT 6,
            event_types text[] NOT NULL DEFAULT
                ARRAY['new_candidate', 'due_soon', 'overdue', 'run_failed']::text[],
            eventual_delivery_kinds text[] NOT NULL DEFAULT ARRAY['overdue']::text[],
            connector_analysis text NOT NULL DEFAULT 'candidate_first',
            todo_promotion text NOT NULL DEFAULT 'manual',
            external_actions text NOT NULL DEFAULT 'approval_required',
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_settings PRIMARY KEY (tenant_id, user_id),
            CONSTRAINT fk_user_settings_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_user_settings_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_user_settings_timezone CHECK (char_length(timezone) BETWEEN 1 AND 100),
            CONSTRAINT ck_user_settings_quiet_hours CHECK (quiet_hours_start <> quiet_hours_end),
            CONSTRAINT ck_user_settings_daily_cap CHECK (daily_cap BETWEEN 0 AND 100),
            CONSTRAINT ck_user_settings_event_types CHECK (
                event_types <@ ARRAY['new_candidate', 'due_soon', 'overdue', 'run_failed']::text[]
            ),
            CONSTRAINT ck_user_settings_eventual_delivery CHECK (
                eventual_delivery_kinds <@ ARRAY['due_soon', 'overdue']::text[]
            ),
            CONSTRAINT ck_user_settings_connector_analysis
                CHECK (connector_analysis IN ('off', 'candidate_first')),
            CONSTRAINT ck_user_settings_todo_promotion CHECK (todo_promotion = 'manual'),
            CONSTRAINT ck_user_settings_external_actions
                CHECK (external_actions = 'approval_required'),
            CONSTRAINT ck_user_settings_version CHECK (version > 0)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_settings CASCADE;")
