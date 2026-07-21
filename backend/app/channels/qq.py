"""QQ / IM outbound client + inbound signature verification (ADR-026, milestone 4).

The IM channel is intentionally adapter-shaped so a second backend (the official
``qq-botpy`` WebSocket SDK, Telegram, …) can drop in later. v1 targets a
self-hosted **OneBot v11 / aiocqhttp** HTTP API (go-cqhttp / Lagrange / AstrBot):

- **Inbound**: the bot framework POSTs message events to ``/channels/qq/webhook``.
  Each body is HMAC-SHA1 signed (``X-Signature: sha1=<hex>``) with the shared
  ``qq_webhook_secret``; :func:`verify_signature` is constant-time.
- **Outbound**: replies + approval previews are pushed via ``POST
  {api_base}/send_private_msg`` (``Authorization: Bearer <access_token>``).

``qq_kind="disabled"`` (default) returns a :class:`RecordingQQClient` that records
sends instead of performing them, so dev/tests never touch the network and the
"simulate inbound" UI lane works without a real bot.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import settings


@dataclass(frozen=True)
class OutboundMessage:
    """One pushed IM message (recorded by the mock, sent by the real client)."""

    to: str
    text: str


class QQClient(Protocol):
    """Outbound seam: push a private message to a QQ user id."""

    async def send_private(self, user_id: str, text: str) -> bool: ...


@dataclass
class RecordingQQClient:
    """Offline mock: records sends without touching the network (dev/tests)."""

    sent: list[OutboundMessage] = field(default_factory=list)

    async def send_private(self, user_id: str, text: str) -> bool:
        self.sent.append(OutboundMessage(to=user_id, text=text))
        return True


@dataclass
class OneBotQQClient:
    """OneBot v11 / aiocqhttp HTTP API client (go-cqhttp / Lagrange / AstrBot)."""

    api_base: str
    access_token: str
    timeout_seconds: int = 15

    async def send_private(self, user_id: str, text: str) -> bool:
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as http:
                resp = await http.post(
                    f"{self.api_base.rstrip('/')}/send_private_msg",
                    json={"user_id": int(user_id), "message": text},
                    headers=headers,
                )
        except (httpx.HTTPError, ValueError):
            return False
        if resp.status_code != 200:
            return False
        body = resp.json()
        return bool(body.get("status") == "ok" or body.get("retcode") == 0)


def build_qq_client() -> QQClient:
    """Return the configured outbound client (mock unless ``qq_kind='onebot'``)."""
    if settings.qq_kind == "onebot":
        return OneBotQQClient(
            api_base=settings.qq_api_base,
            access_token=settings.qq_access_token,
        )
    return RecordingQQClient()


def sign_body(secret: str, body: bytes) -> str:
    """Return the OneBot ``sha1=<hex>`` signature for a raw request body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time HMAC-SHA1 verification of an inbound webhook body.

    An empty configured secret means "unsigned mode" (accept) — only sensible for
    a fully trusted localhost bridge; production should always set a secret.
    """
    if not secret:
        return True
    if not header:
        return False
    return hmac.compare_digest(sign_body(secret, body), header.strip())
