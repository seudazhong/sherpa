"""Persist the complete bounded RuntimeSession exec request for worker-owned 202 jobs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-03

Redis carries only the durable exec row id. The worker reads the command and timeout
from Postgres; ``command_preview`` remains the only API/audit projection.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("project_exec_runs", sa.Column("command_text", sa.Text(), nullable=True))
    op.add_column(
        "project_exec_runs",
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
    )
    # Existing rows predate async dispatch. Their preview was the complete command under
    # the old 500-char service cap, and the configured timeout is the honest default.
    op.execute(
        """
        UPDATE project_exec_runs
        SET command_text = command_preview,
            timeout_seconds = 120
        WHERE command_text IS NULL OR timeout_seconds IS NULL
        """
    )
    op.alter_column("project_exec_runs", "command_text", nullable=False)
    op.alter_column("project_exec_runs", "timeout_seconds", nullable=False)
    op.create_check_constraint(
        "ck_per_command",
        "project_exec_runs",
        "char_length(command_text) BETWEEN 1 AND 4000",
    )
    op.create_check_constraint(
        "ck_per_timeout",
        "project_exec_runs",
        "timeout_seconds BETWEEN 1 AND 900",
    )


def downgrade() -> None:
    op.drop_constraint("ck_per_timeout", "project_exec_runs", type_="check")
    op.drop_constraint("ck_per_command", "project_exec_runs", type_="check")
    op.drop_column("project_exec_runs", "timeout_seconds")
    op.drop_column("project_exec_runs", "command_text")
