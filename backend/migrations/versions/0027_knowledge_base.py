"""knowledge base: sources/versions/chunks/jobs/evidence + sherpa_text (ADR-036, KB1)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-26

Source-backed document knowledge (ADR-036). Canonical = sources + versions +
immutable snapshots; derived = chunks + lexical tsvector + embeddings. Separate from
memory_passages. Tables build unconditionally; the CJK lexical config (`sherpa_text`
via zhparser) is created **best-effort** — present in the combined postgres-zhparser
image, gracefully skipped elsewhere (e.g. CI on a vanilla image), where the lexical
branch stays dormant until the combined image is used. All tables carry tenant_id +
composite tenant-scoped keys (ADR-015).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    # Best-effort CJK: zhparser + the stable `sherpa_text` config. Degrade gracefully
    # where zhparser is unavailable so `alembic upgrade head` stays portable.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS zhparser;
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'sherpa_text') THEN
                CREATE TEXT SEARCH CONFIGURATION sherpa_text (PARSER = zhparser);
                ALTER TEXT SEARCH CONFIGURATION sherpa_text
                    ADD MAPPING FOR a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z
                    WITH simple;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'zhparser/sherpa_text unavailable (%); knowledge lexical branch dormant until the combined image is used', SQLERRM;
        END $$;
        """
    )

    op.execute("""
        CREATE TABLE embedding_profiles (
            tenant_id  uuid NOT NULL,
            id         uuid NOT NULL,
            name       text NOT NULL,
            provider   text NOT NULL,
            model      text NOT NULL,
            dim        integer NOT NULL,
            normalize  text NOT NULL DEFAULT 'cosine',
            privacy    text NOT NULL DEFAULT 'local',
            is_active  boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_embedding_profiles PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_ep_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT ck_ep_dim CHECK (dim BETWEEN 1 AND 4096),
            CONSTRAINT ck_ep_privacy CHECK (privacy IN ('local','external'))
        );
    """)

    op.execute("""
        CREATE TABLE knowledge_sources (
            tenant_id          uuid NOT NULL,
            id                 uuid NOT NULL,
            user_id            uuid NOT NULL,
            source_kind        text NOT NULL DEFAULT 'file',
            file_id            uuid,
            display_name       text NOT NULL,
            visibility         text NOT NULL DEFAULT 'private',
            trust_level        text NOT NULL DEFAULT 'untrusted',
            status             text NOT NULL DEFAULT 'queued',
            active_version_id  uuid,
            desired_generation integer NOT NULL DEFAULT 1,
            tombstoned_at      timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_knowledge_sources PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_ks_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_ks_user FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_ks_status CHECK (status IN ('queued','parsing','chunking','embedding','ready','stale','failed','deleting')),
            CONSTRAINT ck_ks_kind CHECK (source_kind IN ('file')),
            CONSTRAINT ck_ks_visibility CHECK (visibility IN ('private'))
        );
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_sources_owner "
        "ON knowledge_sources (tenant_id, user_id, updated_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_sources_file "
        "ON knowledge_sources (tenant_id, file_id) WHERE file_id IS NOT NULL;"
    )

    op.execute("""
        CREATE TABLE knowledge_source_versions (
            tenant_id             uuid NOT NULL,
            id                    uuid NOT NULL,
            source_id             uuid NOT NULL,
            generation            integer NOT NULL,
            expected_file_version integer,
            expected_file_hash    bytea,
            snapshot_object_key   text NOT NULL,
            parser_version        text NOT NULL,
            pipeline_version      text NOT NULL,
            embedding_profile_id  uuid NOT NULL,
            language              text,
            status                text NOT NULL DEFAULT 'building',
            chunk_count           integer NOT NULL DEFAULT 0,
            failure_code          text,
            idempotency_key       text NOT NULL,
            created_at            timestamptz NOT NULL DEFAULT now(),
            activated_at          timestamptz,
            CONSTRAINT pk_ksv PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_ksv_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_ksv_source FOREIGN KEY (tenant_id, source_id) REFERENCES knowledge_sources (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_ksv_profile FOREIGN KEY (tenant_id, embedding_profile_id) REFERENCES embedding_profiles (tenant_id, id),
            CONSTRAINT ck_ksv_status CHECK (status IN ('building','ready','failed','superseded')),
            CONSTRAINT uq_ksv_idem UNIQUE (tenant_id, source_id, idempotency_key)
        );
    """)
    op.execute(
        "CREATE INDEX ix_ksv_source ON knowledge_source_versions (tenant_id, source_id, generation DESC);"
    )

    op.execute("""
        CREATE TABLE knowledge_chunks (
            tenant_id    uuid NOT NULL,
            id           uuid NOT NULL,
            source_id    uuid NOT NULL,
            version_id   uuid NOT NULL,
            ordinal      integer NOT NULL,
            text_content text NOT NULL,
            token_count  integer NOT NULL,
            heading_path text,
            page         integer,
            char_offset  integer,
            content_hash bytea NOT NULL,
            lexical_text text NOT NULL,
            embedding    vector(1024) NOT NULL,
            fts          tsvector,
            created_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_knowledge_chunks PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_kc_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_kc_version FOREIGN KEY (tenant_id, version_id) REFERENCES knowledge_source_versions (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_kc_text_bound CHECK (octet_length(text_content) <= 65536)
        );
    """)
    op.execute("CREATE INDEX ix_kc_version ON knowledge_chunks (tenant_id, version_id, ordinal);")
    op.execute("CREATE INDEX ix_kc_fts ON knowledge_chunks USING GIN (fts);")
    op.execute(
        "CREATE INDEX ix_kc_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);"
    )

    op.execute("""
        CREATE TABLE knowledge_ingestion_jobs (
            tenant_id          uuid NOT NULL,
            id                 uuid NOT NULL,
            source_id          uuid NOT NULL,
            version_id         uuid,
            generation         integer NOT NULL,
            stage              text NOT NULL DEFAULT 'queued',
            lease_owner        text,
            lease_expires_at   timestamptz,
            attempt            integer NOT NULL DEFAULT 0,
            termination_reason text,
            idempotency_key    text NOT NULL,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_kij PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_kij_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_kij_source FOREIGN KEY (tenant_id, source_id) REFERENCES knowledge_sources (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_kij_stage CHECK (stage IN ('queued','claiming','snapshot','parse','chunk','embed','activate','done','failed')),
            CONSTRAINT uq_kij_idem UNIQUE (tenant_id, idempotency_key)
        );
    """)
    op.execute(
        "CREATE INDEX ix_kij_active ON knowledge_ingestion_jobs (tenant_id, stage, lease_expires_at) "
        "WHERE stage NOT IN ('done','failed');"
    )

    op.execute("""
        CREATE TABLE knowledge_retrieval_evidence (
            tenant_id               uuid NOT NULL,
            id                      uuid NOT NULL,
            user_id                 uuid NOT NULL,
            retrieval_invocation_id uuid NOT NULL,
            run_id                  uuid,
            tool_call_id            text,
            citation_ref            text NOT NULL,
            source_id               uuid NOT NULL,
            source_version_id       uuid NOT NULL,
            chunk_id                uuid NOT NULL,
            excerpt                 text NOT NULL,
            score                   double precision,
            matched_by              text NOT NULL,
            created_at              timestamptz NOT NULL DEFAULT now(),
            purge_after             timestamptz NOT NULL,
            CONSTRAINT pk_kre PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_kre_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT uq_kre_ref UNIQUE (tenant_id, run_id, citation_ref)
        );
    """)
    op.execute("CREATE INDEX ix_kre_gc ON knowledge_retrieval_evidence (tenant_id, purge_after);")


def downgrade() -> None:
    for tbl in (
        "knowledge_retrieval_evidence",
        "knowledge_ingestion_jobs",
        "knowledge_chunks",
        "knowledge_source_versions",
        "knowledge_sources",
        "embedding_profiles",
    ):
        op.execute(f"DROP TABLE IF EXISTS {tbl};")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS sherpa_text;")
    # Leave the zhparser/vector extensions installed; other objects may rely on them.
