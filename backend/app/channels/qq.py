"""QQ outbound seam (ADR-028, official QQ bot).

The outbound interface is deliberately small: :meth:`QQClient.send_private` pushes
a private (C2C) message to a QQ user openid. The real client
(:class:`app.channels.qq_official.QQOfficialSender`) sends via the official
``qq-botpy`` HTTP API (``post_c2c_message``, passive reply keyed by the triggering
``msg_id``). :class:`RecordingQQClient` records instead of sending so dev/tests and
the "simulate inbound" UI lane work without a real bot.

``build_qq_client()`` returns the recording client by default; the worker builds a
real :class:`~app.channels.qq_official.QQOfficialSender` from the stored, sealed
config when delivering a reply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OutboundMessage:
    """One pushed QQ message (recorded by the mock, sent by the real client)."""

    to: str
    text: str
    msg_id: str | None = None


class QQClient(Protocol):
    """Outbound seam: push a private (C2C) message to a QQ user openid.

    ``msg_id`` is the triggering inbound message id used for a passive reply; when
    ``None`` the client attempts an active push (quota-limited).
    """

    async def send_private(self, user_id: str, text: str, msg_id: str | None = None) -> bool: ...


@dataclass
class RecordingQQClient:
    """Offline mock: records sends without touching the network (dev/tests)."""

    sent: list[OutboundMessage] = field(default_factory=list)

    async def send_private(self, user_id: str, text: str, msg_id: str | None = None) -> bool:
        self.sent.append(OutboundMessage(to=user_id, text=text, msg_id=msg_id))
        return True


def build_qq_client() -> QQClient:
    """Return the default (recording) QQ client.

    The worker builds a real ``QQOfficialSender`` from stored config for actual
    delivery; this default keeps offline/test paths and the API notify seam safe.
    """
    return RecordingQQClient()
