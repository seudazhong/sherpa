"""allow 'debug' durability for observability events (ADR-033, Phase OBS-A)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-24

events-and-effects.md §2.7 defines optional per-LLM-call `model.request` /
`model.response` journal events at durability `debug` (diagnostic, not an audit
fact — complement the ephemeral OTel spans). The original CHECK only admitted
`durable` / `presentation`; this reconciles it with the events contract. No data
change; additive only.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_journal DROP CONSTRAINT ck_event_journal_durability")
    op.execute(
        "ALTER TABLE event_journal ADD CONSTRAINT ck_event_journal_durability "
        "CHECK (durability IN ('durable', 'presentation', 'debug'))"
    )


def downgrade() -> None:
    # Reverting requires no 'debug' rows to exist.
    op.execute("DELETE FROM event_journal WHERE durability = 'debug'")
    op.execute("ALTER TABLE event_journal DROP CONSTRAINT ck_event_journal_durability")
    op.execute(
        "ALTER TABLE event_journal ADD CONSTRAINT ck_event_journal_durability "
        "CHECK (durability IN ('durable', 'presentation'))"
    )
