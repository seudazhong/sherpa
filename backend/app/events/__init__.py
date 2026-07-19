"""Event journal + outbox write path."""

from __future__ import annotations

from app.events.journal import AppendedEvent, append_event

__all__ = ["append_event", "AppendedEvent"]
