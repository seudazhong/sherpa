"""relax todos for agent-created todos (m-tools T4)

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-21

Until now every todo had to be the accepted result of a Gmail candidate
(`source_candidate_id NOT NULL`, `source='gmail_candidate'`). The agent tool
surface (ADR-023) lets the agent create standalone todos, so we allow
`source='agent'` with a NULL `source_candidate_id`. The candidate backlink FK is
MATCH SIMPLE, so NULL-linked rows skip it; the unique on `source_candidate_id`
still holds (NULLs are distinct in Postgres).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE todos ALTER COLUMN source_candidate_id DROP NOT NULL;")
    op.execute("ALTER TABLE todos DROP CONSTRAINT ck_todos_source;")
    op.execute(
        "ALTER TABLE todos ADD CONSTRAINT ck_todos_source "
        "CHECK (source IN ('gmail_candidate', 'agent'));"
    )
    op.execute(
        "ALTER TABLE todos ADD CONSTRAINT ck_todos_source_candidate_link CHECK ("
        "(source = 'gmail_candidate' AND source_candidate_id IS NOT NULL) "
        "OR (source = 'agent' AND source_candidate_id IS NULL));"
    )


def downgrade() -> None:
    op.execute("DELETE FROM todos WHERE source = 'agent';")
    op.execute("ALTER TABLE todos DROP CONSTRAINT ck_todos_source_candidate_link;")
    op.execute("ALTER TABLE todos DROP CONSTRAINT ck_todos_source;")
    op.execute(
        "ALTER TABLE todos ADD CONSTRAINT ck_todos_source CHECK (source = 'gmail_candidate');"
    )
    op.execute("ALTER TABLE todos ALTER COLUMN source_candidate_id SET NOT NULL;")
