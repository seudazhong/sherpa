"""extractions + generations + candidates (connector analysis pipeline)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20

Raw DDL from contracts/data-model.md. The candidate->todo link
(fk_candidates_accepted_todo) is deferred to the candidate-lifecycle task (M2
#17) which creates the todos table; candidates here are always pending.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE extractions (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            connector_item_id uuid NOT NULL,
            run_id uuid NOT NULL,
            extraction_version integer NOT NULL,
            extractor_version text NOT NULL,
            output_schema_version smallint NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            attempts integer NOT NULL DEFAULT 0,
            error_redacted text,
            started_at timestamptz,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_extractions PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_extractions_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_extractions_item
                FOREIGN KEY (tenant_id, connector_item_id)
                REFERENCES connector_items (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_extractions_run
                FOREIGN KEY (tenant_id, run_id) REFERENCES runs (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_extractions_item_version
                UNIQUE (tenant_id, connector_item_id, extraction_version),
            CONSTRAINT uq_extractions_chain
                UNIQUE (tenant_id, id, connector_item_id, extraction_version),
            CONSTRAINT ck_extractions_versions
                CHECK (extraction_version > 0 AND output_schema_version > 0),
            CONSTRAINT ck_extractions_status
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
            CONSTRAINT ck_extractions_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_extractions_error_bound
                CHECK (error_redacted IS NULL OR octet_length(error_redacted) <= 16384),
            CONSTRAINT ck_extractions_completed CHECK (
                (status IN ('pending', 'running') AND completed_at IS NULL)
                OR (status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
            )
        );
    """)
    op.execute(
        "CREATE INDEX ix_extractions_tenant_status_created "
        "ON extractions (tenant_id, status, created_at);"
    )

    op.execute("""
        CREATE TABLE generations (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            trace_id uuid NOT NULL,
            run_id uuid NOT NULL,
            extraction_id uuid,
            purpose text NOT NULL,
            provider text NOT NULL,
            model text NOT NULL,
            prompt_version text NOT NULL,
            response_schema_version smallint,
            status text NOT NULL,
            attempt integer NOT NULL DEFAULT 1,
            input_tokens bigint NOT NULL DEFAULT 0,
            output_tokens bigint NOT NULL DEFAULT 0,
            cached_input_tokens bigint NOT NULL DEFAULT 0,
            cost_usd numeric(20, 8) NOT NULL DEFAULT 0,
            latency_ms integer,
            started_at timestamptz NOT NULL,
            completed_at timestamptz NOT NULL,
            CONSTRAINT pk_generations PRIMARY KEY (tenant_id, id),
            CONSTRAINT uq_generations_chain UNIQUE (tenant_id, id, extraction_id),
            CONSTRAINT fk_generations_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_generations_trace
                FOREIGN KEY (tenant_id, trace_id) REFERENCES traces (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_generations_run
                FOREIGN KEY (tenant_id, run_id) REFERENCES runs (tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_generations_extraction
                FOREIGN KEY (tenant_id, extraction_id) REFERENCES extractions (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_generations_purpose
                CHECK (purpose IN ('web_chat', 'candidate_extraction', 'digest')),
            CONSTRAINT ck_generations_extraction_purpose CHECK (
                (purpose = 'candidate_extraction' AND extraction_id IS NOT NULL)
                OR (purpose IN ('web_chat', 'digest') AND extraction_id IS NULL)
            ),
            CONSTRAINT ck_generations_status CHECK (status IN ('succeeded', 'failed')),
            CONSTRAINT ck_generations_usage CHECK (
                attempt > 0 AND input_tokens >= 0 AND output_tokens >= 0
                AND cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens
                AND cost_usd >= 0 AND (latency_ms IS NULL OR latency_ms >= 0)
                AND completed_at >= started_at
            ),
            CONSTRAINT ck_generations_schema_version
                CHECK (response_schema_version IS NULL OR response_schema_version > 0)
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_generations_one_per_extraction
            ON generations (tenant_id, extraction_id) WHERE extraction_id IS NOT NULL;
    """)
    op.execute(
        "CREATE INDEX ix_generations_tenant_run ON generations (tenant_id, run_id, started_at);"
    )

    op.execute("""
        CREATE TABLE candidates (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            extraction_id uuid NOT NULL,
            generation_id uuid NOT NULL,
            ordinal integer NOT NULL,
            dedupe_key text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            title varchar(500) NOT NULL,
            description text,
            due_at timestamptz,
            priority text NOT NULL DEFAULT 'medium',
            confidence numeric(5, 4) NOT NULL,
            rationale_redacted text,
            source_excerpt_redacted text,
            accepted_todo_id uuid,
            decided_by_user_id uuid,
            decided_at timestamptz,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_candidates PRIMARY KEY (tenant_id, id),
            CONSTRAINT uq_candidates_dedupe UNIQUE (tenant_id, dedupe_key),
            CONSTRAINT uq_candidates_extraction_ordinal
                UNIQUE (tenant_id, extraction_id, ordinal),
            CONSTRAINT uq_candidates_chain
                UNIQUE (tenant_id, id, generation_id, extraction_id),
            CONSTRAINT uq_candidates_todo_link UNIQUE (tenant_id, id, accepted_todo_id),
            CONSTRAINT fk_candidates_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_candidates_extraction
                FOREIGN KEY (tenant_id, extraction_id) REFERENCES extractions (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_candidates_generation_extraction
                FOREIGN KEY (tenant_id, generation_id, extraction_id)
                REFERENCES generations (tenant_id, id, extraction_id) ON DELETE RESTRICT,
            CONSTRAINT fk_candidates_decider
                FOREIGN KEY (tenant_id, decided_by_user_id) REFERENCES users (tenant_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_candidates_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_candidates_dedupe_key CHECK (char_length(dedupe_key) BETWEEN 1 AND 512),
            CONSTRAINT ck_candidates_status
                CHECK (status IN ('pending', 'accepted', 'edited', 'dismissed')),
            CONSTRAINT ck_candidates_priority CHECK (priority IN ('low', 'medium', 'high')),
            CONSTRAINT ck_candidates_confidence CHECK (confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_candidates_description_bound
                CHECK (description IS NULL OR octet_length(description) <= 65536),
            CONSTRAINT ck_candidates_rationale_bound
                CHECK (rationale_redacted IS NULL OR octet_length(rationale_redacted) <= 16384),
            CONSTRAINT ck_candidates_excerpt_bound
                CHECK (source_excerpt_redacted IS NULL
                       OR octet_length(source_excerpt_redacted) <= 16384),
            CONSTRAINT ck_candidates_decision CHECK (
                (status = 'pending' AND accepted_todo_id IS NULL
                 AND decided_by_user_id IS NULL AND decided_at IS NULL)
                OR (status IN ('accepted', 'edited') AND accepted_todo_id IS NOT NULL
                    AND decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)
                OR (status = 'dismissed' AND accepted_todo_id IS NULL
                    AND decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)
            ),
            CONSTRAINT ck_candidates_version CHECK (version > 0)
        );
    """)
    op.execute(
        "CREATE INDEX ix_candidates_tenant_status_created "
        "ON candidates (tenant_id, status, created_at DESC);"
    )


def downgrade() -> None:
    for table in ("candidates", "generations", "extractions"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
