"""memory passages: pgvector archival/RAG memory (milestone 1c)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-22

The user-private archival/RAG tier of ADR-004: longer notes the agent stores and
recalls by hybrid similarity (lexical FTS + vector). Requires the `vector`
extension (pgvector image). Embeddings are text-embedding-3-small (1536 dims) via
the configured OpenAI-compatible provider. Per architect-review §pgvector: store
the embedding model + a content hash (dedupe) + a generated tsvector for the
lexical branch; every query is tenant+user filtered before ranking. Exact
tenant-filtered search is correct at v1 (single-user) scale; an HNSW index is
added for future scale.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("""
        CREATE TABLE memory_passages (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            text_content text NOT NULL,
            embedding vector(1536) NOT NULL,
            embedding_model text NOT NULL,
            content_hash bytea NOT NULL,
            source text NOT NULL DEFAULT 'agent',
            created_at timestamptz NOT NULL DEFAULT now(),
            fts tsvector GENERATED ALWAYS AS (to_tsvector('english', text_content)) STORED,
            CONSTRAINT pk_memory_passages PRIMARY KEY (tenant_id, id),
            CONSTRAINT fk_memory_passages_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_memory_passages_user
                FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
                ON DELETE CASCADE,
            CONSTRAINT ck_memory_passages_text_bound
                CHECK (octet_length(text_content) <= 65536),
            CONSTRAINT ck_memory_passages_hash
                CHECK (octet_length(content_hash) = 32),
            CONSTRAINT uq_memory_passages_dedupe
                UNIQUE (tenant_id, user_id, content_hash)
        );
    """)
    op.execute(
        "CREATE INDEX ix_memory_passages_tenant_user "
        "ON memory_passages (tenant_id, user_id, created_at DESC);"
    )
    op.execute("CREATE INDEX ix_memory_passages_fts ON memory_passages USING GIN (fts);")
    op.execute(
        "CREATE INDEX ix_memory_passages_embedding "
        "ON memory_passages USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_passages;")
    # Leave the `vector` extension installed; other objects may rely on it.
