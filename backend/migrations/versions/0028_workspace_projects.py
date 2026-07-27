"""workspace projects: projects, snapshots, entries, import jobs + sessions.project_id (ADR-037, W2a)

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-27

Workspace Projects W2a (ADR-037): a Project is a named durable development state.
Canonical = ``projects`` + immutable ``project_snapshots`` + ``project_snapshot_entries``
pointing at the ADR-030 content-addressed, deduped, ref-counted ``storage_blobs`` (shared
with Drive: unchanged bytes never multiply quota). W2a only ever creates ``reason='import'``
snapshots (blank/template/archive). ``project_import_jobs`` is the durable archive-import
job (lease + idempotency + named termination reason) that realizes events §2.9's
``project.lifecycle`` stages. ``sessions.project_id`` is the immutable Project-bound Chat
binding (NULL = General chat). All tables carry ``tenant_id`` + composite tenant-scoped
keys (ADR-015).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE projects (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            name text NOT NULL,
            description text,
            status text NOT NULL DEFAULT 'active',
            current_snapshot_id uuid,
            default_branch_label text NOT NULL DEFAULT 'main',
            source_status text NOT NULL DEFAULT 'unbound',
            used_bytes bigint NOT NULL DEFAULT 0,
            last_activity_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_projects PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_projects_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_projects_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_projects_status CHECK (status IN ('active','archived','deleting')),
            CONSTRAINT ck_projects_source_status CHECK (source_status IN ('unbound')),
            CONSTRAINT ck_projects_name CHECK (char_length(name) BETWEEN 1 AND 200),
            CONSTRAINT ck_projects_used CHECK (used_bytes >= 0)
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_projects_name ON projects (tenant_id, user_id, name) "
        "WHERE status <> 'deleting';"
    )
    op.execute(
        "CREATE INDEX ix_projects_recent ON projects (tenant_id, user_id, last_activity_at DESC);"
    )

    op.execute("""
        CREATE TABLE project_snapshots (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            parent_id uuid,
            reason text NOT NULL,
            entry_count integer NOT NULL DEFAULT 0,
            size_bytes bigint NOT NULL DEFAULT 0,
            source_oid text,
            pinned boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_project_snapshots PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_ps_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_ps_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_ps_parent FOREIGN KEY (tenant_id, parent_id)
                REFERENCES project_snapshots (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_ps_reason CHECK (reason IN ('import','save','checkpoint','sync')),
            CONSTRAINT ck_ps_counts CHECK (entry_count >= 0 AND size_bytes >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX ix_ps_project ON project_snapshots (tenant_id, project_id, created_at DESC);"
    )

    op.execute("""
        CREATE TABLE project_snapshot_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            snapshot_id uuid NOT NULL,
            user_id uuid NOT NULL,
            path text NOT NULL,
            entry_kind text NOT NULL,
            content_hash bytea,
            size_bytes bigint NOT NULL DEFAULT 0,
            executable boolean NOT NULL DEFAULT false,
            symlink_target text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pse PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pse_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pse_snapshot FOREIGN KEY (tenant_id, snapshot_id)
                REFERENCES project_snapshots (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pse_blob FOREIGN KEY (tenant_id, user_id, content_hash)
                REFERENCES storage_blobs (tenant_id, user_id, content_hash) ON DELETE RESTRICT,
            CONSTRAINT ck_pse_kind CHECK (entry_kind IN ('file','dir','symlink')),
            CONSTRAINT ck_pse_path CHECK (
                char_length(path) BETWEEN 1 AND 1024
                AND path NOT LIKE '/%' AND path NOT LIKE '%..%'),
            CONSTRAINT ck_pse_file_blob CHECK (entry_kind <> 'file' OR content_hash IS NOT NULL)
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_pse_path "
        "ON project_snapshot_entries (tenant_id, snapshot_id, path);"
    )
    op.execute(
        "CREATE INDEX ix_pse_blob ON project_snapshot_entries (tenant_id, user_id, content_hash) "
        "WHERE content_hash IS NOT NULL;"
    )

    # Durable archive-import job (events §2.9 realization; mirrors knowledge_ingestion_job).
    op.execute("""
        CREATE TABLE project_import_jobs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            user_id uuid NOT NULL,
            create_kind text NOT NULL,
            stage text NOT NULL DEFAULT 'queued',
            idempotency_key text NOT NULL,
            staging_object_key text,
            archive_bytes bigint NOT NULL DEFAULT 0,
            entry_count integer,
            size_bytes bigint,
            termination_reason text,
            attempt integer NOT NULL DEFAULT 0,
            lease_owner text,
            lease_expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_project_import_jobs PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pij_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pij_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_pij_stage
                CHECK (stage IN ('queued','staged','activated','done','failed')),
            CONSTRAINT ck_pij_kind CHECK (create_kind IN ('archive')),
            CONSTRAINT uq_pij_idem UNIQUE (tenant_id, idempotency_key)
        );
    """)
    op.execute(
        "CREATE INDEX ix_pij_recover ON project_import_jobs (tenant_id, stage, lease_expires_at) "
        "WHERE stage NOT IN ('done','failed');"
    )
    op.execute(
        "CREATE INDEX ix_pij_project ON project_import_jobs (tenant_id, project_id);"
    )

    # Project-bound Chat: immutable binding of a session to one Project (NULL = General).
    op.execute("ALTER TABLE sessions ADD COLUMN project_id uuid;")
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_project "
        "FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) "
        "ON DELETE RESTRICT;"
    )
    op.execute(
        "CREATE INDEX ix_sessions_project ON sessions (tenant_id, project_id) "
        "WHERE project_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sessions_project;")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_project;")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS project_id;")
    op.execute("DROP TABLE IF EXISTS project_import_jobs;")
    op.execute("DROP TABLE IF EXISTS project_snapshot_entries;")
    op.execute("DROP TABLE IF EXISTS project_snapshots;")
    op.execute("DROP TABLE IF EXISTS projects;")
