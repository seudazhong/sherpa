"""recurring schedules / general cron (ADR-031, Phase CRON)

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-23

Generalizes ``schedules`` from reminder/digest-only into a recurring scheduler:
- cadence columns (daily/cron/interval/weekly/monthly/once),
- a new ``agent_task`` action carrying a bounded ``prompt``,
- expanded delivery channels (web/digest_email/email/qq),
- ``schedule_firings.run_id`` linking an agent_task firing to the run it triggered.

Existing rows are backfilled: ``daily_digest`` -> ``cadence_kind='daily'`` (already
the default), ``todo_reminder`` -> ``cadence_kind='once'``. Frozen v1 CHECKs are
dropped and re-added (relaxed) rather than edited in place.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE schedules ADD COLUMN cadence_kind text NOT NULL DEFAULT 'daily';")
    op.execute("ALTER TABLE schedules ADD COLUMN cron_expr text;")
    op.execute("ALTER TABLE schedules ADD COLUMN interval_seconds integer;")
    op.execute("ALTER TABLE schedules ADD COLUMN weekly_days text;")
    op.execute("ALTER TABLE schedules ADD COLUMN monthly_day smallint;")
    op.execute("ALTER TABLE schedules ADD COLUMN prompt text;")

    # Backfill: one-time todo reminders are 'once'; digests stay 'daily'.
    op.execute("UPDATE schedules SET cadence_kind = 'once' WHERE kind = 'todo_reminder';")

    op.execute("ALTER TABLE schedules DROP CONSTRAINT ck_schedules_kind;")
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_kind "
        "CHECK (kind IN ('todo_reminder', 'daily_digest', 'agent_task'));"
    )

    op.execute("ALTER TABLE schedules DROP CONSTRAINT ck_schedules_delivery_channel;")
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_delivery_channel "
        "CHECK (delivery_channel IN ('web', 'digest_email', 'email', 'qq'));"
    )

    op.execute("ALTER TABLE schedules DROP CONSTRAINT ck_schedules_kind_target;")
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_kind_target CHECK ("
        "  (kind = 'todo_reminder' AND todo_id IS NOT NULL AND reminder_kind IS NOT NULL"
        "       AND local_time IS NULL AND prompt IS NULL)"
        "  OR (kind = 'daily_digest' AND todo_id IS NULL AND reminder_kind IS NULL"
        "       AND local_time IS NOT NULL AND prompt IS NULL)"
        "  OR (kind = 'agent_task' AND todo_id IS NULL AND reminder_kind IS NULL"
        "       AND prompt IS NOT NULL AND char_length(prompt) BETWEEN 1 AND 8000)"
        ");"
    )

    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_cadence "
        "CHECK (cadence_kind IN ('daily', 'cron', 'interval', 'weekly', 'monthly', 'once'));"
    )
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_cadence_fields CHECK ("
        "  (cadence_kind = 'cron' AND cron_expr IS NOT NULL)"
        "  OR (cadence_kind = 'interval' AND interval_seconds IS NOT NULL"
        "       AND interval_seconds >= 60)"
        "  OR (cadence_kind = 'weekly' AND weekly_days IS NOT NULL AND local_time IS NOT NULL)"
        "  OR (cadence_kind = 'monthly' AND monthly_day BETWEEN 1 AND 31"
        "       AND local_time IS NOT NULL)"
        "  OR (cadence_kind IN ('daily', 'once'))"
        ");"
    )

    op.execute("ALTER TABLE schedule_firings ADD COLUMN run_id uuid;")
    op.execute(
        "ALTER TABLE schedule_firings ADD CONSTRAINT fk_sf_run "
        "FOREIGN KEY (tenant_id, run_id) REFERENCES runs (tenant_id, id) ON DELETE SET NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE schedule_firings DROP CONSTRAINT IF EXISTS fk_sf_run;")
    op.execute("ALTER TABLE schedule_firings DROP COLUMN IF EXISTS run_id;")

    op.execute("ALTER TABLE schedules DROP CONSTRAINT IF EXISTS ck_schedules_cadence_fields;")
    op.execute("ALTER TABLE schedules DROP CONSTRAINT IF EXISTS ck_schedules_cadence;")

    op.execute("ALTER TABLE schedules DROP CONSTRAINT ck_schedules_kind_target;")
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_kind_target CHECK ("
        "  (kind = 'todo_reminder' AND todo_id IS NOT NULL AND reminder_kind IS NOT NULL"
        "       AND local_time IS NULL)"
        "  OR (kind = 'daily_digest' AND todo_id IS NULL AND reminder_kind IS NULL"
        "       AND local_time IS NOT NULL)"
        ");"
    )

    op.execute("ALTER TABLE schedules DROP CONSTRAINT ck_schedules_delivery_channel;")
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_delivery_channel "
        "CHECK (delivery_channel IN ('web', 'digest_email'));"
    )

    op.execute("ALTER TABLE schedules DROP CONSTRAINT ck_schedules_kind;")
    op.execute(
        "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_kind "
        "CHECK (kind IN ('todo_reminder', 'daily_digest'));"
    )

    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS prompt;")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS monthly_day;")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS weekly_days;")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS interval_seconds;")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS cron_expr;")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS cadence_kind;")
