"""connectors (Gmail OAuth connector base)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md (connectors). connector_items lands with
the incremental-sync task (M2 #15). Token columns hold a direct AES-256-GCM
envelope under the active KEK (the connectors DDL has no encrypted_dek/aad
columns; AAD is recomputed from row identity at decrypt).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE connectors (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            kind text NOT NULL,
            channel_installation_id text NOT NULL,
            external_account_id text NOT NULL,
            token_enc bytea,
            nonce bytea,
            kek_id text,
            key_version integer,
            token_algorithm text,
            aad_version smallint,
            scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
            status text NOT NULL DEFAULT 'pending_oauth',
            cursor jsonb NOT NULL DEFAULT '{}'::jsonb,
            refresh_version bigint NOT NULL DEFAULT 0,
            last_sync_at timestamptz,
            last_error_redacted text,
            rotated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_connectors PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_connectors_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_connectors_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_connectors_external_account
                UNIQUE (tenant_id, kind, external_account_id),
            CONSTRAINT uq_connectors_installation
                UNIQUE (tenant_id, channel_installation_id),
            CONSTRAINT ck_connectors_kind CHECK (kind = 'gmail'),
            CONSTRAINT ck_connectors_status CHECK (
                status IN ('pending_oauth', 'active', 'syncing', 'degraded',
                           'paused', 'disconnecting', 'revoked', 'error')
            ),
            CONSTRAINT ck_connectors_aead_all_or_none CHECK (
                (token_enc IS NULL AND nonce IS NULL AND kek_id IS NULL
                 AND key_version IS NULL AND token_algorithm IS NULL AND aad_version IS NULL)
                OR (token_enc IS NOT NULL AND nonce IS NOT NULL AND kek_id IS NOT NULL
                    AND key_version IS NOT NULL AND token_algorithm IS NOT NULL
                    AND aad_version IS NOT NULL)
            ),
            CONSTRAINT ck_connectors_active_has_token CHECK (
                status NOT IN ('active', 'syncing', 'degraded', 'paused',
                               'disconnecting', 'error')
                OR token_enc IS NOT NULL
            ),
            CONSTRAINT ck_connectors_revoked_has_no_token CHECK (
                status <> 'revoked'
                OR (token_enc IS NULL AND nonce IS NULL AND kek_id IS NULL
                    AND key_version IS NULL AND token_algorithm IS NULL AND aad_version IS NULL)
            ),
            CONSTRAINT ck_connectors_aead_values CHECK (
                token_enc IS NULL
                OR (octet_length(token_enc) >= 16 AND octet_length(nonce) = 12
                    AND key_version > 0 AND token_algorithm = 'AES-256-GCM' AND aad_version > 0)
            ),
            CONSTRAINT ck_connectors_scopes CHECK (
                cardinality(scopes) <= 16
                AND (status <> 'active' OR cardinality(scopes) > 0)
            ),
            CONSTRAINT ck_connectors_cursor_bound CHECK (octet_length(cursor::text) <= 65536),
            CONSTRAINT ck_connectors_refresh_version CHECK (refresh_version >= 0),
            CONSTRAINT ck_connectors_error_bound CHECK (
                last_error_redacted IS NULL OR octet_length(last_error_redacted) <= 16384
            )
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_connectors_aead_nonce
            ON connectors (tenant_id, kek_id, key_version, nonce)
            WHERE nonce IS NOT NULL;
    """)
    op.execute("CREATE INDEX ix_connectors_tenant_status ON connectors (tenant_id, status);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connectors CASCADE;")
