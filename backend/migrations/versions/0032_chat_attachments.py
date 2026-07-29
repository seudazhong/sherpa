"""chat attachments: typed message parts + per-source vision flag (ADR-043)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-29

Chat attachments (backlog B-6). Attachments are **references** to Drive nodes, not a
second byte store: a user turn may now carry ``parts`` rows of kind ``image`` /
``file_ref`` whose ``content_redacted`` is ``{drive_node_id, version, name,
content_type, size_bytes}``. Bytes stay in Drive (ADR-030), so quota / per-file caps /
versioning / trash / GC are inherited rather than re-invented.

``model_providers.supports_vision`` declares whether a source accepts image content;
when false the assembler degrades an image to an honest text placeholder instead of
sending it and failing (ADR-043 §6, api §10.8).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE parts DROP CONSTRAINT IF EXISTS ck_parts_kind")
    op.execute("""
        ALTER TABLE parts ADD CONSTRAINT ck_parts_kind
            CHECK (kind IN ('text', 'status', 'image', 'file_ref'))
    """)
    op.execute("""
        ALTER TABLE model_providers
            ADD COLUMN IF NOT EXISTS supports_vision boolean NOT NULL DEFAULT true
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE model_providers DROP COLUMN IF EXISTS supports_vision")
    # Attachment parts have no pre-0032 representation: drop them so the narrower
    # CHECK can be restored (the Drive nodes they reference are untouched).
    op.execute("DELETE FROM parts WHERE kind IN ('image', 'file_ref')")
    op.execute("ALTER TABLE parts DROP CONSTRAINT IF EXISTS ck_parts_kind")
    op.execute("""
        ALTER TABLE parts ADD CONSTRAINT ck_parts_kind
            CHECK (kind IN ('text', 'status'))
    """)
