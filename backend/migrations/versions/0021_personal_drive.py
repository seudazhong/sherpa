"""personal drive: storage accounts, blobs, nodes, versions (ADR-030, W1)

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-23

Turns the thin flat ``files`` primitive into a Personal Drive:
- ``storage_accounts``: per-user quota (quota/used/reserved bytes).
- ``storage_blobs``: immutable, content-addressed, reference-counted bytes. Object
  deletion happens only in a GC worker once ``ref_count = 0`` past retention.
- ``drive_nodes``: folders and files as first-class records (not path prefixes),
  with trash (trashed_at/purge_after).
- ``drive_versions``: retained prior file versions, each pointing at a blob.

Cross-store ordering (fixes the old put/delete-before-commit bug): the object is
written before commit and never deleted inline; the DB row is the reference.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE storage_accounts (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            quota_bytes bigint NOT NULL,
            used_bytes bigint NOT NULL DEFAULT 0,
            reserved_bytes bigint NOT NULL DEFAULT 0,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_storage_accounts PRIMARY KEY (tenant_id, user_id),
            CONSTRAINT fk_sa_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_sa_numbers
                CHECK (quota_bytes >= 0 AND used_bytes >= 0 AND reserved_bytes >= 0)
        );
    """)
    op.execute("""
        CREATE TABLE storage_blobs (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            content_hash bytea NOT NULL,
            object_key text NOT NULL,
            size_bytes bigint NOT NULL,
            content_type text NOT NULL DEFAULT 'application/octet-stream',
            ref_count integer NOT NULL DEFAULT 0,
            unreferenced_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_storage_blobs PRIMARY KEY (tenant_id, user_id, content_hash),
            CONSTRAINT fk_sb_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_sb_size CHECK (size_bytes >= 0 AND ref_count >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX ix_sb_gc ON storage_blobs (tenant_id, unreferenced_at) WHERE ref_count = 0;"
    )
    op.execute("""
        CREATE TABLE drive_nodes (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            parent_id uuid,
            node_type text NOT NULL,
            name text NOT NULL,
            content_hash bytea,
            size_bytes bigint NOT NULL DEFAULT 0,
            content_type text NOT NULL DEFAULT 'application/octet-stream',
            version integer NOT NULL DEFAULT 1,
            trashed_at timestamptz,
            purge_after timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_drive_nodes PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_dn_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_dn_parent FOREIGN KEY (tenant_id, parent_id)
                REFERENCES drive_nodes (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_dn_type CHECK (node_type IN ('folder', 'file')),
            CONSTRAINT ck_dn_name
                CHECK (char_length(name) BETWEEN 1 AND 255 AND name NOT LIKE '%/%')
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_dn_sibling_name "
        "ON drive_nodes (tenant_id, user_id, parent_id, name) WHERE trashed_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX ix_dn_parent ON drive_nodes (tenant_id, user_id, parent_id) "
        "WHERE trashed_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX ix_dn_trash ON drive_nodes (tenant_id, user_id, purge_after) "
        "WHERE trashed_at IS NOT NULL;"
    )
    op.execute("""
        CREATE TABLE drive_versions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            node_id uuid NOT NULL,
            user_id uuid NOT NULL,
            version integer NOT NULL,
            content_hash bytea NOT NULL,
            size_bytes bigint NOT NULL,
            content_type text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_drive_versions PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_dv_node FOREIGN KEY (tenant_id, node_id)
                REFERENCES drive_nodes (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT uq_dv_node_version UNIQUE (tenant_id, node_id, version)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS drive_versions;")
    op.execute("DROP TABLE IF EXISTS drive_nodes;")
    op.execute("DROP TABLE IF EXISTS storage_blobs;")
    op.execute("DROP TABLE IF EXISTS storage_accounts;")
