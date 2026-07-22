"""channel configs + per-thread state (ADR-028, official QQ integration)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-22

``channel_configs`` holds one row per (tenant, user, channel) with non-secret
config (app_id, enabled, owner_external_id) plus a sealed secret (AES-256-GCM
under the KEK: secret_enc/secret_nonce/kek_id/key_version). ``channel_thread_state``
remembers the last inbound external message id per session for QQ passive replies.
Both cascade on tenant; channel_configs also cascades on the owning user.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE channel_configs (
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            channel text NOT NULL,
            kind text NOT NULL,
            enabled boolean NOT NULL DEFAULT false,
            app_id text NOT NULL DEFAULT '',
            owner_external_id text NOT NULL DEFAULT '',
            secret_enc bytea NOT NULL DEFAULT '',
            secret_nonce bytea NOT NULL DEFAULT '',
            kek_id text NOT NULL DEFAULT '',
            key_version integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_channel_configs PRIMARY KEY (tenant_id, user_id, channel),
            CONSTRAINT fk_channel_configs_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_channel_configs_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_channel_configs_channel CHECK (char_length(channel) BETWEEN 1 AND 32)
        );
    """)
    op.execute("""
        CREATE TABLE channel_thread_state (
            tenant_id uuid NOT NULL,
            session_id uuid NOT NULL,
            last_inbound_msg_id text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_channel_thread_state PRIMARY KEY (tenant_id, session_id),
            CONSTRAINT fk_channel_thread_state_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_channel_thread_state_session
                FOREIGN KEY (tenant_id, session_id) REFERENCES sessions (tenant_id, id)
                ON DELETE CASCADE
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS channel_thread_state;")
    op.execute("DROP TABLE IF EXISTS channel_configs;")
