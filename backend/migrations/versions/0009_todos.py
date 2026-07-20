"""todos + bidirectional candidate<->todo link

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md. Accepting a candidate creates exactly one
todo in the same transaction using preallocated UUIDs; the two DEFERRABLE
INITIALLY DEFERRED FKs enforce a true one-to-one bidirectional link, validated
at COMMIT.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE todos (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            source_candidate_id uuid NOT NULL,
            source text NOT NULL DEFAULT 'gmail_candidate',
            title varchar(500) NOT NULL,
            description text,
            status text NOT NULL DEFAULT 'open',
            due_at timestamptz,
            snoozed_until timestamptz,
            priority text NOT NULL DEFAULT 'medium',
            completed_at timestamptz,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_todos PRIMARY KEY (tenant_id, id),
            CONSTRAINT uq_todos_source_candidate UNIQUE (tenant_id, source_candidate_id),
            CONSTRAINT fk_todos_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_todos_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_todos_source CHECK (source = 'gmail_candidate'),
            CONSTRAINT ck_todos_status CHECK (status IN ('open', 'completed', 'cancelled')),
            CONSTRAINT ck_todos_priority CHECK (priority IN ('low', 'medium', 'high')),
            CONSTRAINT ck_todos_description_bound
                CHECK (description IS NULL OR octet_length(description) <= 65536),
            CONSTRAINT ck_todos_completion CHECK (
                (status = 'completed' AND completed_at IS NOT NULL AND snoozed_until IS NULL)
                OR (status = 'open' AND completed_at IS NULL)
                OR (status = 'cancelled' AND completed_at IS NULL AND snoozed_until IS NULL)
            ),
            CONSTRAINT ck_todos_version CHECK (version > 0)
        );
    """)
    op.execute("""
        ALTER TABLE candidates
            ADD CONSTRAINT fk_candidates_accepted_todo
            FOREIGN KEY (tenant_id, accepted_todo_id)
            REFERENCES todos (tenant_id, id)
            DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute("""
        ALTER TABLE todos
            ADD CONSTRAINT fk_todos_source_candidate_backlink
            FOREIGN KEY (tenant_id, source_candidate_id, id)
            REFERENCES candidates (tenant_id, id, accepted_todo_id)
            DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute(
        "CREATE INDEX ix_todos_tenant_status_created ON todos (tenant_id, status, created_at DESC);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE candidates DROP CONSTRAINT IF EXISTS fk_candidates_accepted_todo;")
    op.execute("DROP TABLE IF EXISTS todos CASCADE;")
