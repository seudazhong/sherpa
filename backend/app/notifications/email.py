"""Outbound email sender (pluggable) — the single send seam (roadmap unify-note).

Both the ``send_email`` tool and notification digests go through
``build_email_sender()``. v1 default records instead of sending (no account);
``email_kind='agentmail'`` sends via the agent's AgentMail inbox (ADR-027). Bodies
are never logged verbatim.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Protocol

from app.channels.email import AgentMailClient
from app.config import settings

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


class AgentMailEmailSender:
    """Sends via the agent's AgentMail inbox (ADR-027). Delegates to AgentMailClient."""

    def __init__(self) -> None:
        self._client = AgentMailClient(
            api_base=settings.agentmail_api_base,
            api_key=settings.agentmail_api_key,
            inbox_id=settings.agentmail_inbox_id,
        )

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        message_id = await self._client.send(to=to, subject=subject, text=body)
        logger.info(
            "email sent via agentmail",
            extra={"to_present": bool(to), "subject": subject, "ok": bool(message_id)},
        )
        return message_id is not None


_sender = RecordingEmailSender()


def build_email_sender() -> EmailSender:
    """The configured email sender: AgentMail when enabled, else the recording stub."""
    if settings.email_kind == "agentmail":
        return AgentMailEmailSender()
    return _sender
