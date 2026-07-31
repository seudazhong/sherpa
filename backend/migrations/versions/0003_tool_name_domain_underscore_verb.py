"""Tool names are `domain_verb`, not `domain.verb` — the dot is rejected on the wire.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-31

`0002` widened the `ck_pg_tool` CHECK to the dotted `domain.verb` grammar from
ADR-046 §决策1. A live smoke against the configured provider then showed the dot
is not portable: GitHub Copilot (behind the litellm proxy) rejects the whole
request with

    400 Invalid 'tools[0].name': string does not match pattern.
        Expected a string that matches the pattern '^[a-zA-Z0-9_-]+$'.

so with dotted names the agent could make no tool call at all. The unit suite
could not catch this because it runs against the mock provider. Every tool was
renamed `domain.verb` -> `domain_verb` (ADR-046 修订 B), which is both portable
and what Anthropic's own guidance uses (`asana_search`, `jira_search`).

The constraint now requires the two-part `domain_verb` shape. It cannot prove the
prefix is a registered namespace — that belongs to `ToolDescriptor.namespace`
(Phase TR P2.1).
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

_NEW = r"^[a-z][a-z0-9]*_[a-z][a-z0-9_]*$"
_DOTTED = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"


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
        f"CHECK (tool_name ~ '{_DOTTED}'::text)"
    )
