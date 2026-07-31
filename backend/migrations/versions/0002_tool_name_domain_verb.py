"""Widen the tool-name CHECK to the `domain.verb` grammar (ADR-046 §决策1).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31

Every built-in tool was renamed from a mix of `action_domain` / `domain_action`
(measured: 28 / 15 / 4 neither) to a single `domain.verb` namespace. The
`permission_grants.tool_name` CHECK constraint still spelled the old grammar
`^[a-z][a-z0-9_]{0,63}$`, which has no dot, so every grant insert for a renamed
tool (`email_send`) failed with `ck_pg_tool`.

This is a genuine schema change, so it earns its own revision rather than being
folded back into `0001_baseline` — existing databases (including the ADR-044
`<app_db>_test` database, which is retained between runs) must be migrated, not
rebuilt.

Nothing else in the schema constrains a tool name: `effect_invocations` and the
approval tables store it as plain `Text`.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

_NEW = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
_OLD = r"^[a-z][a-z0-9_]{0,63}$"


def upgrade() -> None:
    op.execute("ALTER TABLE permission_grants DROP CONSTRAINT IF EXISTS ck_pg_tool")
    op.execute(
        f"ALTER TABLE permission_grants ADD CONSTRAINT ck_pg_tool "
        f"CHECK (tool_name ~ '{_NEW}'::text)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE permission_grants DROP CONSTRAINT IF EXISTS ck_pg_tool")
    op.execute(
        f"ALTER TABLE permission_grants ADD CONSTRAINT ck_pg_tool "
        f"CHECK (tool_name ~ '{_OLD}'::text)"
    )
