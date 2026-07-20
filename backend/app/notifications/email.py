"""Outbound email sender (pluggable). v1 records instead of sending (no SMTP).

A real SMTP/agentic-email sender implements the same EmailSender protocol and is
selected by config once the account is provided. Bodies are never logged verbatim.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Protocol

logger = logging.getLogger("sherpa.notifications")


@dataclasses.dataclass(frozen=True)
class SentEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> bool: ...


class RecordingEmailSender:
    """v1 stub: records the email and reports success without sending."""

    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.sent.append(SentEmail(to=to, subject=subject, body=body))
        logger.info("email recorded", extra={"to_present": bool(to), "subject": subject})
        return True


_sender = RecordingEmailSender()


def build_email_sender() -> EmailSender:
    """The configured email sender (v1: recording stub; SMTP wires in later)."""
    return _sender
