"""personal files: per-user file workspace metadata (milestone 2)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-22

Per-user file workspace (ADR-012). Blobs live in object storage (MinIO); this
table maps a logical per-user ``path`` → a server-generated ``object_key`` (never
the user's path, to avoid traversal) plus size/content-type/hash/version. One row
per (tenant, user, path); overwriting a path bumps the version. Tenant+user
cascade.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE files (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            path text NOT NULL,
            object_key text NOT NULL,
            size_bytes bigint NOT NULL,
            content_type text NOT NULL DEFAULT 'application/octet-stream',
            content_hash bytea NOT NULL,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_files PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_files_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_files_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_files_path CHECK (char_length(path) BETWEEN 1 AND 1024),
            CONSTRAINT ck_files_size CHECK (size_bytes >= 0),
            CONSTRAINT ck_files_hash CHECK (octet_length(content_hash) = 32),
            CONSTRAINT ck_files_version CHECK (version > 0),
            CONSTRAINT uq_files_path UNIQUE (tenant_id, user_id, path)
        );
    """)
    op.execute("CREATE INDEX ix_files_tenant_user ON files (tenant_id, user_id, path);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS files;")
