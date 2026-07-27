"""workspace projects W2b: github_connections + project_sources + import-job github cols (ADR-038)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-27

Workspace Projects W2b (ADR-038): GitHub one-time import. Adds ``github_connections``
(AEAD credential record, ADR-019, reusing the connectors column shape) + canonical
``project_sources`` provenance (repo id + ref + resolved OID); widens
``projects.source_status`` (W2a was 'unbound' only) with importing/imported/import_failed;
and teaches ``project_import_jobs`` the ``github`` create_kind + the source spec /
credential-reference columns (the token stays in github_connections/vault — never here).
All tables carry ``tenant_id`` + composite tenant-scoped keys (ADR-015).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # GitHub credential record (AEAD, reuses the connectors column shape). One active
    # connection per owner (uq_ghc_owner_active). The token is decrypted ONLY by the
    # import worker at the connector boundary and never leaves it.
    op.execute("""
        CREATE TABLE github_connections (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            auth_kind text NOT NULL,
            account_login text,
            installation_id text,
            token_enc bytea,
            nonce bytea,
            kek_id text,
            key_version integer,
            token_algorithm text,
            aad_version smallint,
            scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
            status text NOT NULL DEFAULT 'pending',
            last_error_redacted text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_github_connections PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_ghc_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_ghc_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_ghc_auth_kind CHECK (auth_kind IN ('pat','app_installation')),
            CONSTRAINT ck_ghc_status CHECK (status IN ('pending','active','revoked','error')),
            CONSTRAINT ck_ghc_aead_all_or_none CHECK (
                (token_enc IS NULL AND nonce IS NULL AND kek_id IS NULL AND key_version IS NULL
                     AND token_algorithm IS NULL AND aad_version IS NULL)
                OR (token_enc IS NOT NULL AND nonce IS NOT NULL AND kek_id IS NOT NULL
                     AND key_version IS NOT NULL AND token_algorithm IS NOT NULL
                     AND aad_version IS NOT NULL)),
            CONSTRAINT ck_ghc_active_has_token CHECK (status <> 'active' OR token_enc IS NOT NULL)
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_ghc_owner_active ON github_connections (tenant_id, user_id) "
        "WHERE status <> 'revoked';"
    )

    # Canonical GitHub source provenance: one row per project once a github import starts.
    op.execute("""
        CREATE TABLE project_sources (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            user_id uuid NOT NULL,
            provider text NOT NULL DEFAULT 'github',
            connection_id uuid,
            repo_external_id text NOT NULL,
            owner text NOT NULL,
            repo text NOT NULL,
            ref_type text NOT NULL,
            ref_name text NOT NULL,
            source_oid text,
            status text NOT NULL DEFAULT 'importing',
            imported_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_project_sources PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_psrc_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_psrc_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_psrc_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_psrc_connection FOREIGN KEY (tenant_id, connection_id)
                REFERENCES github_connections (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_psrc_provider CHECK (provider IN ('github')),
            CONSTRAINT ck_psrc_ref_type CHECK (ref_type IN ('branch','tag','commit')),
            CONSTRAINT ck_psrc_status CHECK (status IN ('importing','imported','import_failed'))
        );
    """)
    op.execute("CREATE UNIQUE INDEX uq_psrc_project ON project_sources (tenant_id, project_id);")

    # projects.source_status widens for W2b (W2a was 'unbound' only).
    op.execute("ALTER TABLE projects DROP CONSTRAINT ck_projects_source_status;")
    op.execute(
        "ALTER TABLE projects ADD CONSTRAINT ck_projects_source_status "
        "CHECK (source_status IN ('unbound','importing','imported','import_failed'));"
    )

    # project_import_jobs learns the github create_kind + source spec / credential ref.
    op.execute("ALTER TABLE project_import_jobs DROP CONSTRAINT ck_pij_kind;")
    op.execute(
        "ALTER TABLE project_import_jobs ADD CONSTRAINT ck_pij_kind "
        "CHECK (create_kind IN ('archive','github'));"
    )
    op.execute("ALTER TABLE project_import_jobs ADD COLUMN connection_id uuid;")
    op.execute("ALTER TABLE project_import_jobs ADD COLUMN source_ref_type text;")
    op.execute("ALTER TABLE project_import_jobs ADD COLUMN source_ref text;")
    op.execute("ALTER TABLE project_import_jobs ADD COLUMN resolved_oid text;")
    op.execute(
        "ALTER TABLE project_import_jobs ADD CONSTRAINT fk_pij_connection "
        "FOREIGN KEY (tenant_id, connection_id) "
        "REFERENCES github_connections (tenant_id, id) ON DELETE RESTRICT;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE project_import_jobs DROP CONSTRAINT IF EXISTS fk_pij_connection;")
    op.execute("ALTER TABLE project_import_jobs DROP COLUMN IF EXISTS resolved_oid;")
    op.execute("ALTER TABLE project_import_jobs DROP COLUMN IF EXISTS source_ref;")
    op.execute("ALTER TABLE project_import_jobs DROP COLUMN IF EXISTS source_ref_type;")
    op.execute("ALTER TABLE project_import_jobs DROP COLUMN IF EXISTS connection_id;")
    op.execute("ALTER TABLE project_import_jobs DROP CONSTRAINT IF EXISTS ck_pij_kind;")
    op.execute(
        "ALTER TABLE project_import_jobs ADD CONSTRAINT ck_pij_kind "
        "CHECK (create_kind IN ('archive'));"
    )
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_source_status;")
    op.execute(
        "ALTER TABLE projects ADD CONSTRAINT ck_projects_source_status "
        "CHECK (source_status IN ('unbound'));"
    )
    op.execute("DROP TABLE IF EXISTS project_sources;")
    op.execute("DROP TABLE IF EXISTS github_connections;")
