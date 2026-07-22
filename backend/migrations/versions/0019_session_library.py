"""session title + run liveness lease (ADR-029, Session Library P0)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-23

Adds a persisted ``sessions.title`` (previously echoed by the API but never
stored) and a Postgres-backed run liveness lease so the Session Library can tell
a live run from a dead worker: ``runs.heartbeat_at`` / ``lease_expires_at`` /
``worker_id``. A run is "live" only while ``status='running'`` AND
``lease_expires_at > now()``; otherwise a running row is stale and must be
recovered, never silently reconnected (ADR-029).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN title text;")
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_title "
        "CHECK (title IS NULL OR char_length(title) BETWEEN 1 AND 200);"
    )
    op.execute("ALTER TABLE runs ADD COLUMN heartbeat_at timestamptz;")
    op.execute("ALTER TABLE runs ADD COLUMN lease_expires_at timestamptz;")
    op.execute("ALTER TABLE runs ADD COLUMN worker_id text;")
    op.execute(
        "CREATE INDEX ix_runs_live_lease ON runs (tenant_id, lease_expires_at) "
        "WHERE status = 'running';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_runs_live_lease;")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS worker_id;")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS lease_expires_at;")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS heartbeat_at;")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_title;")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS title;")
