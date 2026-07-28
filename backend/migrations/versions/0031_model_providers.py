"""model providers: user-configurable multi-source model layer (ADR-041)

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-28

Multi-source model providers (ADR-041; roadmap #8's multi-provider half). Replaces the
env single provider with a DB-backed, user-configured registry: one ``model_providers``
row = one source (``kind`` + ``base_url`` + AEAD-sealed API key + model catalog + default
flag). The API key reuses the connectors AEAD column shape and is sealed under the active
KEK (``security/model_provider_key.py``); it is decrypted ONLY at the ``Provider.stream()``
/ test boundary and never leaves it. ``sessions`` gains a per-conversation model override
(``model_provider_id`` + ``model``). All tables carry ``tenant_id`` + composite keys (ADR-015).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE model_providers (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            kind text NOT NULL,
            display_name text NOT NULL,
            base_url text,
            token_enc bytea,
            nonce bytea,
            kek_id text,
            key_version integer,
            token_algorithm text,
            aad_version smallint,
            models text[] NOT NULL DEFAULT ARRAY[]::text[],
            default_model text,
            enabled boolean NOT NULL DEFAULT true,
            is_default boolean NOT NULL DEFAULT false,
            status text NOT NULL DEFAULT 'pending',
            last_error_redacted text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_model_providers PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_mp_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_mp_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_mp_kind CHECK (kind IN ('openai_compatible','anthropic','gemini')),
            CONSTRAINT ck_mp_status CHECK (status IN ('pending','active','error')),
            CONSTRAINT ck_mp_name CHECK (char_length(display_name) BETWEEN 1 AND 200),
            CONSTRAINT ck_mp_aead_all_or_none CHECK (
                (token_enc IS NULL AND nonce IS NULL AND kek_id IS NULL AND key_version IS NULL
                     AND token_algorithm IS NULL AND aad_version IS NULL)
                OR (token_enc IS NOT NULL AND nonce IS NOT NULL AND kek_id IS NOT NULL
                     AND key_version IS NOT NULL AND token_algorithm IS NOT NULL
                     AND aad_version IS NOT NULL)),
            CONSTRAINT ck_mp_enabled_has_key CHECK (enabled = false OR token_enc IS NOT NULL)
        );
    """)
    # At most ONE default source per owner; unique display name per owner.
    op.execute(
        "CREATE UNIQUE INDEX uq_mp_default ON model_providers (tenant_id, user_id) "
        "WHERE is_default;"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_mp_name ON model_providers (tenant_id, user_id, display_name);"
    )
    op.execute("CREATE INDEX ix_mp_owner ON model_providers (tenant_id, user_id, updated_at DESC);")

    # Per-conversation model override (NULL => global default source + its default_model).
    op.execute("ALTER TABLE sessions ADD COLUMN model_provider_id uuid;")
    op.execute("ALTER TABLE sessions ADD COLUMN model text;")
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_model_provider "
        "FOREIGN KEY (tenant_id, model_provider_id) "
        "REFERENCES model_providers (tenant_id, id) ON DELETE SET NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_model_provider;")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS model;")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS model_provider_id;")
    op.execute("DROP TABLE IF EXISTS model_providers;")
