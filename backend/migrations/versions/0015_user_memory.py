"""user-private core memory (milestone 1a: two-tier memory foundation)

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-21

Bounded key-value "core memory" per (tenant, user) — the v1 user-private tier of
ADR-004. The tenant-shared blocks (multi-user) and the pgvector/RAG passage tier
remain deferred (ADR-012/022). Frozen DDL transcribed from
docs/contracts/data-model.md (user_memory): value bounded to 16 KiB, keys match
^[a-z][a-z0-9_.-]{0,63}$, optimistic version, tenant/user cascade.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_memory (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            memory_key varchar(64) NOT NULL,
            value_text text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_memory
                PRIMARY KEY (tenant_id, user_id, memory_key),
            CONSTRAINT fk_user_memory_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_user_memory_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_user_memory_key
                CHECK (memory_key ~ '^[a-z][a-z0-9_.-]{0,63}$'),
            CONSTRAINT ck_user_memory_value_bound
                CHECK (octet_length(value_text) <= 16384),
            CONSTRAINT ck_user_memory_version
                CHECK (version > 0)
        );
    """)
    op.execute(
        "CREATE INDEX ix_user_memory_tenant_user "
        "ON user_memory (tenant_id, user_id, updated_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_memory;")
