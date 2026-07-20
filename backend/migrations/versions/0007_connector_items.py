"""connector_items (Gmail message provenance)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md (connector_items). Immutable provenance:
a changed item creates another row and flips the former row's is_latest;
content is deduped by (connector, provider_item_id, revision).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE connector_items (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            connector_id uuid NOT NULL,
            provider_item_id text NOT NULL,
            revision text NOT NULL,
            provider_thread_id text,
            received_at timestamptz NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            content_digest bytea NOT NULL,
            content_json jsonb,
            is_latest boolean NOT NULL DEFAULT true,
            deletion_state text NOT NULL DEFAULT 'present',
            source_deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_connector_items PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_connector_items_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_connector_items_connector
                FOREIGN KEY (tenant_id, connector_id) REFERENCES connectors (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_connector_items_revision
                UNIQUE (tenant_id, connector_id, provider_item_id, revision),
            CONSTRAINT uq_connector_items_id_revision
                UNIQUE (tenant_id, id, revision),
            CONSTRAINT ck_connector_items_ids CHECK (
                char_length(provider_item_id) BETWEEN 1 AND 512
                AND char_length(revision) BETWEEN 1 AND 255
            ),
            CONSTRAINT ck_connector_items_digest CHECK (octet_length(content_digest) = 32),
            CONSTRAINT ck_connector_items_content_bound CHECK (
                content_json IS NULL OR octet_length(content_json::text) <= 262144
            ),
            CONSTRAINT ck_connector_items_deletion CHECK (
                (deletion_state = 'present' AND source_deleted_at IS NULL)
                OR (deletion_state = 'source_deleted' AND source_deleted_at IS NOT NULL)
                OR (deletion_state = 'purged' AND source_deleted_at IS NOT NULL
                    AND content_json IS NULL)
            )
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_connector_items_latest
            ON connector_items (tenant_id, connector_id, provider_item_id)
            WHERE is_latest;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_items CASCADE;")
