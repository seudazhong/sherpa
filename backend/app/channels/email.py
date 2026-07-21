"""Agentic email: AgentMail HTTP client + inbound Svix verification (ADR-027, milestone 5).

The agent owns a mailbox identity (an AgentMail inbox). This module is the single
low-level AgentMail seam:

- **Outbound**: :class:`AgentMailClient.send` / ``.reply`` (``POST
  /v0/inboxes/{inbox}/messages/send`` / ``/reply``, ``Authorization: Bearer``).
  The notification :class:`~app.notifications.email.AgentMailEmailSender` and the
  agentic-email channel both delegate here, so there is one send path (roadmap
  unify-note): ``send_email`` tool + digests + IM-less replies share it.
- **Inbound**: AgentMail delivers ``message.received`` events via Svix. The body is
  verified with :func:`verify_svix_signature` (HMAC-SHA256 over
  ``{svix-id}.{svix-timestamp}.{body}``, base64 secret after the ``whsec_`` prefix).

``email_kind="recording"`` (default) returns a :class:`RecordingEmailClient` that
records instead of calling the network, so dev/tests stay offline and the
"simulate inbound" UI lane works without a live mailbox.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import settings


@dataclass(frozen=True)
class SentEmailRecord:
    """One outbound email (recorded by the mock, sent by the real client)."""

    to: str
    subject: str
    text: str


class EmailChannelClient(Protocol):
    """Outbound seam for the agent's mailbox."""

    async def send(self, *, to: str, subject: str, text: str) -> str | None: ...


@dataclass
class RecordingEmailClient:
    """Offline mock: records sends without touching the network (dev/tests)."""

    sent: list[SentEmailRecord] = field(default_factory=list)

    async def send(self, *, to: str, subject: str, text: str) -> str | None:
        self.sent.append(SentEmailRecord(to=to, subject=subject, text=text))
        return "recorded"


@dataclass
class AgentMailClient:
    """AgentMail API client (agent-owned inbox). Returns the message id or None."""

    api_base: str
    api_key: str
    inbox_id: str
    timeout_seconds: int = 30

    async def send(self, *, to: str, subject: str, text: str) -> str | None:
        if not self.api_key or not self.inbox_id:
            return None
        url = f"{self.api_base.rstrip('/')}/v0/inboxes/{self.inbox_id}/messages/send"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as http:
                resp = await http.post(
                    url, json={"to": to, "subject": subject, "text": text}, headers=headers
                )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        body = resp.json()
        message_id = body.get("message_id")
        return str(message_id) if message_id else "sent"


def build_email_channel_client() -> EmailChannelClient:
    """Return the configured mailbox client (mock unless ``email_kind='agentmail'``)."""
    if settings.email_kind == "agentmail":
        return AgentMailClient(
            api_base=settings.agentmail_api_base,
            api_key=settings.agentmail_api_key,
            inbox_id=settings.agentmail_inbox_id,
        )
    return RecordingEmailClient()


def verify_svix_signature(
    secret: str,
    body: bytes,
    *,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
) -> bool:
    """Constant-time Svix webhook verification (HMAC-SHA256), as AgentMail uses.

    Signature base is ``{svix_id}.{svix_timestamp}.{body}``; the secret is base64
    after the ``whsec_`` prefix; ``svix-signature`` is a space-delimited list of
    ``v1,<base64>`` — any match wins. An empty configured secret means "unsigned
    mode" (accept) — only for a trusted bridge; production should set a secret.
    """
    if not secret:
        return True
    if not (svix_id and svix_timestamp and svix_signature):
        return False
    key = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    try:
        key_bytes = base64.b64decode(key)
    except (ValueError, TypeError):
        return False
    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key_bytes, signed, hashlib.sha256).digest()).decode()
    for part in svix_signature.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False
