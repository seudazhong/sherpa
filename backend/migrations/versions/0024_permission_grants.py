"""pre-authorization grants (ADR-034, Phase APPROVALS)

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-24

Owner-configured rules that let the core loop auto-allow a matching external action
instead of asking (e.g. a send_email recipient allowlist). Owner-only; matched
actions still record their effect + an audit receipt. No wildcards in v1.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE permission_grants (
            tenant_id   uuid NOT NULL,
            id          uuid NOT NULL,
            user_id     uuid NOT NULL,
            tool_name   text NOT NULL,
            match_json  jsonb NOT NULL,
            created_via text NOT NULL DEFAULT 'manual',
            created_at  timestamptz NOT NULL DEFAULT now(),
            revoked_at  timestamptz,
            CONSTRAINT pk_permission_grants PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pg_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pg_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_pg_tool CHECK (tool_name ~ '^[a-z][a-z0-9_]{0,63}$'),
            CONSTRAINT ck_pg_created_via CHECK (created_via IN ('manual', 'always')),
            CONSTRAINT ck_pg_match_bound CHECK (octet_length(match_json::text) <= 8192)
        );
    """)
    op.execute(
        "CREATE INDEX ix_pg_active ON permission_grants (tenant_id, user_id, tool_name) "
        "WHERE revoked_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS permission_grants;")
