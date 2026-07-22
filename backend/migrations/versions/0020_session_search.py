"""session search projection (ADR-029 P1)

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-23

Derived, rebuildable search projection over the canonical session spine. One row
per indexable unit (title / user_message / assistant_message / tool / action)
with typed deep-link anchors (never mixes ``messages.seq`` with
``event_journal.session_seq``), a generated ``simple`` FTS vector, an
application-generated CJK bigram vector, and a trigram index on normalized text.

Populated by an inline per-session reindex (app.search.indexer) that is a pure
function of the canonical rows, so it is deterministically rebuildable. Deletion
/redaction blanks ``content_text`` and sets ``redacted_at`` (generated vectors
become empty and never match).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("""
        CREATE TABLE session_search_entries (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            session_id uuid NOT NULL,
            source_kind text NOT NULL,
            source_id text NOT NULL,
            anchor_kind text NOT NULL,
            anchor_id text NOT NULL,
            run_id uuid,
            message_seq bigint,
            event_session_seq bigint,
            channel text NOT NULL,
            content_text text NOT NULL,
            normalized_text text NOT NULL,
            cjk_terms text NOT NULL DEFAULT '',
            occurred_at timestamptz NOT NULL,
            projection_version integer NOT NULL DEFAULT 1,
            redacted_at timestamptz,
            fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', content_text)) STORED,
            cjk_fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', cjk_terms)) STORED,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_session_search_entries PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_sse_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_sse_session FOREIGN KEY (tenant_id, session_id)
                REFERENCES sessions (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT uq_sse_source
                UNIQUE (tenant_id, source_kind, source_id, projection_version),
            CONSTRAINT ck_sse_kind CHECK (
                source_kind IN
                ('title', 'user_message', 'assistant_message', 'tool', 'action')
            ),
            CONSTRAINT ck_sse_anchor_kind CHECK (
                anchor_kind IN ('message', 'event', 'audit', 'session')
            ),
            CONSTRAINT ck_sse_content_bound CHECK (octet_length(content_text) <= 32768)
        );
    """)
    op.execute(
        "CREATE INDEX ix_sse_browse ON session_search_entries "
        "(tenant_id, user_id, occurred_at DESC, id) WHERE redacted_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX ix_sse_session_anchor ON session_search_entries "
        "(tenant_id, session_id, occurred_at DESC);"
    )
    op.execute("CREATE INDEX ix_sse_fts ON session_search_entries USING GIN (fts);")
    op.execute("CREATE INDEX ix_sse_cjk_fts ON session_search_entries USING GIN (cjk_fts);")
    op.execute(
        "CREATE INDEX ix_sse_normalized_trgm ON session_search_entries "
        "USING GIN (normalized_text gin_trgm_ops);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_search_entries;")
