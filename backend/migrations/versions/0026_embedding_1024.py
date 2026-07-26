"""embedding dim 1536 -> 1024: bundled ollama bge-m3 (ADR-032)

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-26

ADR-032 decouples embeddings from the chat provider and defaults to a bundled
local `ollama` model (`bge-m3`, multilingual incl. CJK, 1024-d). The
`memory_passages.embedding` column width MUST equal `EMBEDDING_DIM`. This is free
right now because `memory_passages` has 0 rows, so it is a pure re-type (drop the
HNSW index -> ALTER COLUMN TYPE -> recreate the index). A populated table would
instead require a full re-embed. Contract: data-model.md §"Post-v1 contract
additions" (ADR-032).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_passages_embedding;")
    op.execute("ALTER TABLE memory_passages ALTER COLUMN embedding TYPE vector(1024);")
    op.execute(
        "CREATE INDEX ix_memory_passages_embedding "
        "ON memory_passages USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_passages_embedding;")
    op.execute("ALTER TABLE memory_passages ALTER COLUMN embedding TYPE vector(1536);")
    op.execute(
        "CREATE INDEX ix_memory_passages_embedding "
        "ON memory_passages USING hnsw (embedding vector_cosine_ops);"
    )
