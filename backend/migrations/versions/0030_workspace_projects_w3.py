"""workspace projects W3: working copies + overlay + change sets + artifacts + sandbox runs (ADR-040 + ADR-039)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-27

Workspace Projects W3 (ADR-040 product/data + ADR-039 isolation): a Project-bound
Chat's first mutating action opens a DURABLE task working copy (spans turns) from the
current Project head; each execution materializes a one-time disposable scratch copy
into a hardened, network-disabled sandbox (never the snapshot/blob store/credentials).

Canonical / durable (data-model §Projects W3):
  - ``projects.head_generation`` (cheap monotonic CAS token; bumped in the same tx that
    advances ``current_snapshot_id``)
  - ``project_working_copies`` + ``project_working_copy_entries`` (durable overlay)
  - ``project_change_sets`` + ``project_change_set_entries`` (reviewable projection)
  - ``project_artifacts`` (run outputs; charge quota only after Keep/Export)
  - ``project_sandbox_runs`` (link run<->working copy + boundary outcome; scratch/container
    ids are operational-only, never recovery truth)

Rebuildable cache (NEVER a table): the materialized scratch tree, package cache, prepared
image, warm container. File bytes are the shared ADR-030 content-addressed ``storage_blobs``;
bytes/credentials never enter the journal. All tables carry ``tenant_id`` + composite
tenant-scoped keys (ADR-015).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cheap monotonic head-generation CAS token (bumped with current_snapshot_id).
    op.execute("ALTER TABLE projects ADD COLUMN head_generation integer NOT NULL DEFAULT 0;")

    # One DURABLE pending task working copy, owned by exactly one Project-bound Chat.
    op.execute("""
        CREATE TABLE project_working_copies (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            session_id uuid NOT NULL,
            user_id uuid NOT NULL,
            base_snapshot_id uuid NOT NULL,
            base_head_generation integer NOT NULL,
            state text NOT NULL DEFAULT 'open',
            version integer NOT NULL DEFAULT 0,
            fence_token bigint NOT NULL DEFAULT 0,
            lease_owner text,
            lease_expires_at timestamptz,
            reserved_bytes bigint NOT NULL DEFAULT 0,
            overlay_entry_count integer NOT NULL DEFAULT 0,
            overlay_bytes bigint NOT NULL DEFAULT 0,
            last_run_id uuid,
            last_boundary_at timestamptz,
            expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pwc PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pwc_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pwc_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pwc_session FOREIGN KEY (tenant_id, session_id)
                REFERENCES sessions (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pwc_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pwc_base FOREIGN KEY (tenant_id, base_snapshot_id)
                REFERENCES project_snapshots (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_pwc_state CHECK (state IN
                ('open','ready_for_review','saved','discarded','conflicted','expired')),
            CONSTRAINT ck_pwc_reserved CHECK (reserved_bytes >= 0),
            CONSTRAINT ck_pwc_overlay CHECK (overlay_entry_count >= 0 AND overlay_bytes >= 0)
        );
    """)
    # AT MOST ONE live (open/ready_for_review) working copy per Project-bound Chat.
    op.execute(
        "CREATE UNIQUE INDEX uq_pwc_live_session ON project_working_copies (tenant_id, session_id) "
        "WHERE state IN ('open','ready_for_review');"
    )
    op.execute(
        "CREATE INDEX ix_pwc_project ON project_working_copies "
        "(tenant_id, project_id, updated_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_pwc_reap ON project_working_copies (tenant_id, expires_at) "
        "WHERE state IN ('open','ready_for_review');"
    )

    # The DURABLE overlay: the working copy's delta vs its base snapshot.
    op.execute("""
        CREATE TABLE project_working_copy_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            working_copy_id uuid NOT NULL,
            user_id uuid NOT NULL,
            path text NOT NULL,
            change_kind text NOT NULL,
            entry_kind text NOT NULL DEFAULT 'file',
            content_hash bytea,
            size_bytes bigint NOT NULL DEFAULT 0,
            executable boolean NOT NULL DEFAULT false,
            symlink_target text,
            fence_token bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pwce PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pwce_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pwce_wc FOREIGN KEY (tenant_id, working_copy_id)
                REFERENCES project_working_copies (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pwce_blob FOREIGN KEY (tenant_id, user_id, content_hash)
                REFERENCES storage_blobs (tenant_id, user_id, content_hash) ON DELETE RESTRICT,
            CONSTRAINT ck_pwce_change CHECK (change_kind IN ('added','modified','deleted')),
            CONSTRAINT ck_pwce_kind CHECK (entry_kind IN ('file','dir','symlink')),
            CONSTRAINT ck_pwce_path CHECK (
                char_length(path) BETWEEN 1 AND 1024
                AND path NOT LIKE '/%' AND path NOT LIKE '%..%'),
            CONSTRAINT ck_pwce_blob_presence CHECK (
                (change_kind IN ('added','modified') AND entry_kind = 'file')
                    = (content_hash IS NOT NULL))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_pwce_path "
        "ON project_working_copy_entries (tenant_id, working_copy_id, path);"
    )

    # A bounded, REVIEWABLE change set produced at an execution boundary.
    op.execute("""
        CREATE TABLE project_change_sets (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            working_copy_id uuid NOT NULL,
            session_id uuid NOT NULL,
            run_id uuid,
            base_snapshot_id uuid NOT NULL,
            fence_token bigint NOT NULL,
            state text NOT NULL DEFAULT 'open',
            added_count integer NOT NULL DEFAULT 0,
            modified_count integer NOT NULL DEFAULT 0,
            deleted_count integer NOT NULL DEFAULT 0,
            artifact_count integer NOT NULL DEFAULT 0,
            changed_bytes bigint NOT NULL DEFAULT 0,
            diff_bytes bigint NOT NULL DEFAULT 0,
            truncated boolean NOT NULL DEFAULT false,
            created_snapshot_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pcs PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pcs_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pcs_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pcs_wc FOREIGN KEY (tenant_id, working_copy_id)
                REFERENCES project_working_copies (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pcs_base FOREIGN KEY (tenant_id, base_snapshot_id)
                REFERENCES project_snapshots (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_pcs_state CHECK (state IN
                ('open','applied','discarded','superseded','conflicted')),
            CONSTRAINT ck_pcs_counts CHECK (
                added_count >= 0 AND modified_count >= 0 AND deleted_count >= 0
                AND artifact_count >= 0 AND changed_bytes >= 0 AND diff_bytes >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX ix_pcs_wc ON project_change_sets (tenant_id, working_copy_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_pcs_project ON project_change_sets "
        "(tenant_id, project_id, created_at DESC);"
    )

    # One row per reviewable file change in a change set.
    op.execute("""
        CREATE TABLE project_change_set_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            change_set_id uuid NOT NULL,
            path text NOT NULL,
            change_kind text NOT NULL,
            old_content_hash bytea,
            new_content_hash bytea,
            size_bytes bigint NOT NULL DEFAULT 0,
            executable boolean NOT NULL DEFAULT false,
            is_binary boolean NOT NULL DEFAULT false,
            diff_object_key text,
            diff_truncated boolean NOT NULL DEFAULT false,
            selected boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pcse PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_pcse_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_pcse_cs FOREIGN KEY (tenant_id, change_set_id)
                REFERENCES project_change_sets (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_pcse_change CHECK (change_kind IN ('added','modified','deleted')),
            CONSTRAINT ck_pcse_path CHECK (
                char_length(path) BETWEEN 1 AND 1024
                AND path NOT LIKE '/%' AND path NOT LIKE '%..%')
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_pcse_path "
        "ON project_change_set_entries (tenant_id, change_set_id, path);"
    )

    # Run OUTPUTS that are not project files (test/build logs, generated reports).
    op.execute("""
        CREATE TABLE project_artifacts (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            working_copy_id uuid,
            run_id uuid,
            user_id uuid NOT NULL,
            name text NOT NULL,
            kind text NOT NULL DEFAULT 'file',
            content_hash bytea,
            size_bytes bigint NOT NULL DEFAULT 0,
            mime text,
            retention text NOT NULL DEFAULT 'ephemeral',
            retained_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_part PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_part_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_part_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_part_wc FOREIGN KEY (tenant_id, working_copy_id)
                REFERENCES project_working_copies (tenant_id, id) ON DELETE SET NULL,
            CONSTRAINT fk_part_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_part_blob FOREIGN KEY (tenant_id, user_id, content_hash)
                REFERENCES storage_blobs (tenant_id, user_id, content_hash) ON DELETE RESTRICT,
            CONSTRAINT ck_part_kind CHECK (kind IN ('file','log','report')),
            CONSTRAINT ck_part_retention CHECK (retention IN ('ephemeral','retained','expired'))
        );
    """)
    op.execute("CREATE INDEX ix_part_wc ON project_artifacts (tenant_id, working_copy_id);")
    op.execute(
        "CREATE INDEX ix_part_project ON project_artifacts "
        "(tenant_id, project_id, created_at DESC);"
    )

    # A sandbox execution: links a run to a working copy + records bounded operational
    # metadata + the durable execution-boundary outcome. scratch/container refs = caches.
    op.execute("""
        CREATE TABLE project_sandbox_runs (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            project_id uuid NOT NULL,
            working_copy_id uuid NOT NULL,
            session_id uuid NOT NULL,
            run_id uuid NOT NULL,
            user_id uuid NOT NULL,
            base_snapshot_id uuid NOT NULL,
            fence_token bigint NOT NULL,
            state text NOT NULL DEFAULT 'materializing',
            scratch_ref text,
            container_ref text,
            warm_until timestamptz,
            exit_code integer,
            timed_out boolean NOT NULL DEFAULT false,
            termination_reason text,
            persisted_boundary_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_psr PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_psr_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_psr_project FOREIGN KEY (tenant_id, project_id)
                REFERENCES projects (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_psr_wc FOREIGN KEY (tenant_id, working_copy_id)
                REFERENCES project_working_copies (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_psr_session FOREIGN KEY (tenant_id, session_id)
                REFERENCES sessions (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_psr_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_psr_state CHECK (state IN
                ('materializing','running','persisted','failed','timed_out'))
        );
    """)
    op.execute(
        "CREATE INDEX ix_psr_wc ON project_sandbox_runs "
        "(tenant_id, working_copy_id, created_at DESC);"
    )
    op.execute("CREATE INDEX ix_psr_run ON project_sandbox_runs (tenant_id, run_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_sandbox_runs;")
    op.execute("DROP TABLE IF EXISTS project_artifacts;")
    op.execute("DROP TABLE IF EXISTS project_change_set_entries;")
    op.execute("DROP TABLE IF EXISTS project_change_sets;")
    op.execute("DROP TABLE IF EXISTS project_working_copy_entries;")
    op.execute("DROP TABLE IF EXISTS project_working_copies;")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS head_generation;")
