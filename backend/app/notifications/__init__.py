"""Notifications: firing delivery + pluggable email sender."""

from __future__ import annotations

from app.notifications.delivery import (
    deliver_due_firings,
    deliver_firing,
    ensure_settings,
    in_quiet_hours,
)
from app.notifications.email import EmailSender, RecordingEmailSender, build_email_sender

__all__ = [
    "deliver_due_firings",
    "deliver_firing",
    "ensure_settings",
    "in_quiet_hours",
    "EmailSender",
    "RecordingEmailSender",
    "build_email_sender",
]
