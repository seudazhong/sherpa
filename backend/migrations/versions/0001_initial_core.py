"""initial core spine: tenants/users/identities/sessions/runs/messages/parts

Revision ID: 0001
Revises:
Create Date: 2026-07-20

Raw DDL transcribed from docs/contracts/data-model.md (source of truth) to
preserve composite PKs, CHECKs, partial/functional indexes and deferred FKs.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tenants (
            tenant_id uuid NOT NULL,
            slug varchar(63) NOT NULL,
            display_name varchar(200) NOT NULL,
            kind text NOT NULL DEFAULT 'personal',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_tenants PRIMARY KEY (tenant_id),
            CONSTRAINT uq_tenants_slug UNIQUE (slug),
            CONSTRAINT ck_tenants_slug CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
            CONSTRAINT ck_tenants_kind CHECK (kind = 'personal')
        );
    """)

    op.execute("""
        CREATE TABLE users (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            email text NOT NULL,
            display_name varchar(200) NOT NULL,
            status text NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_users PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT ck_users_email CHECK (char_length(email) BETWEEN 3 AND 320),
            CONSTRAINT ck_users_status CHECK (status IN ('active', 'disabled'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX ux_users_tenant_lower_email ON users (tenant_id, lower(email));"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_users_one_active_owner_per_tenant "
        "ON users (tenant_id) WHERE status = 'active';"
    )

    op.execute("""
        CREATE TABLE identities (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            channel text NOT NULL,
            channel_installation_id text NOT NULL,
            scope_type text NOT NULL,
            external_scope_id text NOT NULL,
            external_actor_id text NOT NULL,
            verified_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_identities PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_identities_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_identities_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_identities_canonical_scope UNIQUE (
                tenant_id, channel, channel_installation_id, scope_type, external_scope_id
            ),
            CONSTRAINT ck_identities_channel CHECK (char_length(channel) BETWEEN 1 AND 32),
            CONSTRAINT ck_identities_installation
                CHECK (char_length(channel_installation_id) BETWEEN 1 AND 255),
            CONSTRAINT ck_identities_scope_type CHECK (char_length(scope_type) BETWEEN 1 AND 32),
            CONSTRAINT ck_identities_external_scope
                CHECK (char_length(external_scope_id) BETWEEN 1 AND 512),
            CONSTRAINT ck_identities_external_actor
                CHECK (char_length(external_actor_id) BETWEEN 1 AND 512)
        );
    """)
    op.execute("CREATE INDEX ix_identities_tenant_user ON identities (tenant_id, user_id);")

    op.execute("""
        CREATE TABLE sessions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            identity_id uuid,
            umo_key text NOT NULL,
            channel text NOT NULL,
            channel_installation_id text NOT NULL,
            scope_type text NOT NULL,
            external_scope_id text NOT NULL,
            status text NOT NULL DEFAULT 'open',
            admitted_seq bigint,
            promoted_seq bigint,
            fence_token bigint NOT NULL DEFAULT 0,
            input_tokens_rollup bigint NOT NULL DEFAULT 0,
            output_tokens_rollup bigint NOT NULL DEFAULT 0,
            cost_usd_rollup numeric(20, 8) NOT NULL DEFAULT 0,
            last_activity_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_sessions PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_sessions_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_sessions_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_sessions_identity FOREIGN KEY (tenant_id, identity_id)
                REFERENCES identities (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_sessions_umo_key UNIQUE (tenant_id, umo_key),
            CONSTRAINT uq_sessions_canonical_scope UNIQUE (
                tenant_id, channel, channel_installation_id, scope_type, external_scope_id
            ),
            CONSTRAINT ck_sessions_umo_key CHECK (char_length(umo_key) BETWEEN 1 AND 1024),
            CONSTRAINT ck_sessions_status CHECK (status IN ('open', 'archived', 'deleted')),
            CONSTRAINT ck_sessions_sequences CHECK (
                (admitted_seq IS NULL AND promoted_seq IS NULL)
                OR (
                    admitted_seq IS NOT NULL AND admitted_seq > 0
                    AND (promoted_seq IS NULL OR promoted_seq BETWEEN 1 AND admitted_seq)
                )
            ),
            CONSTRAINT ck_sessions_rollups CHECK (
                fence_token >= 0 AND input_tokens_rollup >= 0
                AND output_tokens_rollup >= 0 AND cost_usd_rollup >= 0
            )
        );
    """)
    op.execute(
        "CREATE INDEX ix_sessions_tenant_user_activity "
        "ON sessions (tenant_id, user_id, last_activity_at DESC);"
    )

    op.execute("""
        CREATE TABLE runs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            session_id uuid,
            run_kind text NOT NULL,
            admitted_seq bigint,
            status text NOT NULL DEFAULT 'queued',
            attempt integer NOT NULL DEFAULT 0,
            fence_token bigint NOT NULL DEFAULT 0,
            prompt_version text NOT NULL,
            deadline_at timestamptz,
            started_at timestamptz,
            settled_at timestamptz,
            error_redacted text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_runs PRIMARY KEY (tenant_id, id),
            CONSTRAINT uq_runs_session_binding UNIQUE (tenant_id, id, session_id),
            CONSTRAINT fk_runs_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_runs_session FOREIGN KEY (tenant_id, session_id)
                REFERENCES sessions (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_runs_kind CHECK (
                run_kind IN ('web_chat', 'gmail_sync', 'candidate_extraction', 'schedule_delivery')
            ),
            CONSTRAINT ck_runs_status CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled',
                           'needs_reconciliation')
            ),
            CONSTRAINT ck_runs_numbers CHECK (
                attempt >= 0 AND fence_token >= 0 AND (admitted_seq IS NULL OR admitted_seq > 0)
            ),
            CONSTRAINT ck_runs_session_admission
                CHECK (session_id IS NOT NULL OR admitted_seq IS NULL),
            CONSTRAINT ck_runs_error_bound
                CHECK (error_redacted IS NULL OR octet_length(error_redacted) <= 16384),
            CONSTRAINT ck_runs_settled_time CHECK (
                (status IN ('queued', 'running') AND settled_at IS NULL)
                OR (status IN ('succeeded', 'failed', 'cancelled', 'needs_reconciliation')
                    AND settled_at IS NOT NULL)
            )
        );
    """)
    op.execute(
        "CREATE INDEX ix_runs_tenant_status_created ON runs (tenant_id, status, created_at);"
    )
    op.execute(
        "CREATE INDEX ix_runs_tenant_session_created "
        "ON runs (tenant_id, session_id, created_at DESC) WHERE session_id IS NOT NULL;"
    )

    op.execute("""
        CREATE TABLE messages (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            session_id uuid NOT NULL,
            run_id uuid,
            author_user_id uuid,
            seq bigint NOT NULL,
            role text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_messages PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_messages_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_messages_session FOREIGN KEY (tenant_id, session_id)
                REFERENCES sessions (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_messages_run FOREIGN KEY (tenant_id, run_id)
                REFERENCES runs (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_messages_author FOREIGN KEY (tenant_id, author_user_id)
                REFERENCES users (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_messages_session_seq UNIQUE (tenant_id, session_id, seq),
            CONSTRAINT ck_messages_seq CHECK (seq > 0),
            CONSTRAINT ck_messages_role CHECK (role IN ('user', 'assistant', 'system'))
        );
    """)
    op.execute(
        "CREATE INDEX ix_messages_tenant_session_created "
        "ON messages (tenant_id, session_id, created_at);"
    )

    op.execute("""
        CREATE TABLE parts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            message_id uuid NOT NULL,
            ordinal integer NOT NULL,
            kind text NOT NULL,
            content_redacted jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_parts PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_parts_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_parts_message FOREIGN KEY (tenant_id, message_id)
                REFERENCES messages (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_parts_message_ordinal UNIQUE (tenant_id, message_id, ordinal),
            CONSTRAINT ck_parts_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_parts_kind CHECK (kind IN ('text', 'status')),
            CONSTRAINT ck_parts_content_bound
                CHECK (octet_length(content_redacted::text) <= 65536)
        );
    """)

    # Deferred pointer FKs make admitted_seq/promoted_seq real transcript positions.
    op.execute("""
        ALTER TABLE sessions
            ADD CONSTRAINT fk_sessions_admitted_message
                FOREIGN KEY (tenant_id, id, admitted_seq)
                REFERENCES messages (tenant_id, session_id, seq)
                DEFERRABLE INITIALLY DEFERRED,
            ADD CONSTRAINT fk_sessions_promoted_message
                FOREIGN KEY (tenant_id, id, promoted_seq)
                REFERENCES messages (tenant_id, session_id, seq)
                DEFERRABLE INITIALLY DEFERRED;
    """)
    op.execute("""
        ALTER TABLE runs
            ADD CONSTRAINT fk_runs_admitted_message
                FOREIGN KEY (tenant_id, session_id, admitted_seq)
                REFERENCES messages (tenant_id, session_id, seq)
                DEFERRABLE INITIALLY DEFERRED;
    """)


def downgrade() -> None:
    for table in ("parts", "messages", "runs", "sessions", "identities", "users", "tenants"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
