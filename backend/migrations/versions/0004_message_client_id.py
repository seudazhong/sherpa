"""messages.client_message_id for durable-admission idempotency

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-20

Backs the api.md prompt-idempotency contract (client_message_id unique within
(tenant_id, session_id)); the frozen data-model omitted the column, so this
additive migration closes that api<->data-model gap. NULL for agent-authored
messages (assistant/system); set only for client-submitted prompts.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN client_message_id uuid;")
    op.execute("""
        CREATE UNIQUE INDEX uq_messages_client_message_id
            ON messages (tenant_id, session_id, client_message_id)
            WHERE client_message_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_messages_client_message_id;")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS client_message_id;")
