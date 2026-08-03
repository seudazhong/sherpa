"""Add durable RuntimeSession exec dispatch, bounded output, and cancellation fields.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03

Phase TR P4 executes agent and human shell commands through the same durable
``project_exec_runs`` record. Agent-driven rows link to the already-committed effect
invocation so recovery never blindly starts the same command twice. Bounded output
supports the Runs projection; ``cancel_requested_at`` is the cross-process signal used
by the REST Stop path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("project_exec_runs", sa.Column("invocation_id", sa.UUID(), nullable=True))
    op.add_column("project_exec_runs", sa.Column("stdout_head", sa.Text(), nullable=True))
    op.add_column("project_exec_runs", sa.Column("stderr_tail", sa.Text(), nullable=True))
    op.add_column(
        "project_exec_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_per_invocation",
        "project_exec_runs",
        "effect_invocations",
        ["tenant_id", "invocation_id"],
        ["tenant_id", "invocation_id"],
    )
    op.create_index(
        "uq_per_invocation",
        "project_exec_runs",
        ["tenant_id", "invocation_id"],
        unique=True,
        postgresql_where=sa.text("invocation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_per_invocation", table_name="project_exec_runs")
    op.drop_constraint("fk_per_invocation", "project_exec_runs", type_="foreignkey")
    op.drop_column("project_exec_runs", "cancel_requested_at")
    op.drop_column("project_exec_runs", "stderr_tail")
    op.drop_column("project_exec_runs", "stdout_head")
    op.drop_column("project_exec_runs", "invocation_id")
