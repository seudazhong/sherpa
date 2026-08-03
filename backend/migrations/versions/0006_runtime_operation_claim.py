"""Add a durable cross-worker claim for RuntimeSession open/close Docker I/O.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "project_runtime_sessions",
        sa.Column("operation_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "project_runtime_sessions",
        sa.Column("operation_kind", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_prs_operation",
        "project_runtime_sessions",
        """
        (operation_id IS NULL AND operation_kind IS NULL)
        OR (operation_id IS NOT NULL AND operation_kind IN ('open','close'))
        """,
    )


def downgrade() -> None:
    op.drop_constraint("ck_prs_operation", "project_runtime_sessions", type_="check")
    op.drop_column("project_runtime_sessions", "operation_kind")
    op.drop_column("project_runtime_sessions", "operation_id")
