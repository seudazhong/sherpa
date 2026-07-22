"""Official QQ bot adapter (ADR-028): botpy WebSocket inbound + C2C sender.

Two pieces, both built from the DB-stored config (AppID + sealed AppSecret):

- :class:`QQOfficialSender` — a stateless outbound client (implements the
  :class:`app.channels.qq.QQClient` seam). It logs in with the official ``qq-botpy``
  HTTP API and sends a private (C2C) message via ``post_c2c_message``, as a passive
  reply keyed by the triggering ``msg_id`` (active push when ``msg_id`` is ``None``,
  which is quota-limited).
- :class:`QQOfficialClient` + :func:`route_c2c_inbound` — the inbound path. The
  worker runs a botpy ``Client`` whose ``on_c2c_message_create`` routes each private
  message through the generic pipeline (:func:`app.channels.handle_inbound`): a new
  durable run, or an ``approve``/``reject`` approval resolve. The owner allowlist
  (``owner_external_id`` on the config) gates who may drive the agent (single-user
  v1); an empty allowlist accepts any sender (and self-binds nothing).
"""

from __future__ import annotations

import random
import uuid

import botpy
import botpy.message

from app.channels.inbound import handle_inbound
from app.channels.qq import RecordingQQClient
from app.observability import bind_context


class QQOfficialSender:
    """Outbound C2C client via the official qq-botpy HTTP API (login on first send)."""

    def __init__(self, app_id: str, secret: str, *, timeout: int = 30) -> None:
        self._app_id = app_id
        self._secret = secret
        self._timeout = timeout
        self._api: botpy.BotAPI | None = None

    async def _ensure_api(self) -> botpy.BotAPI:
        if self._api is None:
            http = botpy.BotHttp(timeout=self._timeout, app_id=self._app_id, secret=self._secret)
            await http.login(botpy.Token(self._app_id, self._secret))
            self._api = botpy.BotAPI(http=http)
        return self._api

    async def send_private(self, user_id: str, text: str, msg_id: str | None = None) -> bool:
        try:
            api = await self._ensure_api()
            await api.post_c2c_message(
                openid=user_id,
                msg_type=0,
                content=text,
                msg_id=msg_id,
                msg_seq=str(random.randint(1, 1_000_000)),
            )
        except Exception:
            return False
        return True


def build_qq_sender(app_id: str, secret: str) -> QQOfficialSender | RecordingQQClient:
    """Real sender when credentials are present, else the recording mock."""
    if app_id and secret:
        return QQOfficialSender(app_id, secret)
    return RecordingQQClient()


async def test_qq_credentials(app_id: str, secret: str) -> tuple[bool, str]:
    """Attempt an official login with the given AppID/Secret. Returns (ok, detail)."""
    if not app_id or not secret:
        return False, "missing app_id or secret"
    try:
        http = botpy.BotHttp(timeout=15, app_id=app_id, secret=secret)
        robot = await http.login(botpy.Token(app_id, secret))
    except Exception as exc:  # noqa: BLE001 - surfaced as a test result, never raised
        return False, str(exc)[:200]
    name = getattr(robot, "username", None) or getattr(robot, "id", None) or "ok"
    return True, str(name)


async def route_c2c_inbound(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    app_id: str,
    owner_openid: str,
    sender_openid: str,
    content: str,
    msg_id: str,
    reply_sender: QQOfficialSender | RecordingQQClient,
) -> dict[str, str]:
    """Route one inbound C2C message through the generic pipeline. Opens its own tx."""
    from app.db import SessionLocal

    if owner_openid and sender_openid != owner_openid:
        return {"status": "sender_not_allowed"}

    async def _notify(external_id: str, text: str) -> None:
        await reply_sender.send_private(external_id, text, msg_id)

    async with SessionLocal() as db:
        bind_context(tenant_id=str(tenant_id))
        return await handle_inbound(
            db,
            channel="qq",
            installation=app_id or "qq",
            notify=_notify,
            tenant_id=tenant_id,
            user_id=user_id,
            sender=sender_openid,
            text=content,
            message_id=msg_id or None,
        )


class QQOfficialClient(botpy.Client):
    """botpy client that routes inbound C2C messages into Sherpa's loop."""

    def configure(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        app_id: str,
        secret: str,
        owner_openid: str,
    ) -> None:
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._app_id = app_id
        self._owner_openid = owner_openid
        self._sender = build_qq_sender(app_id, secret)

    async def on_c2c_message_create(self, message: botpy.message.C2CMessage) -> None:
        sender_openid = getattr(message.author, "user_openid", "") or ""
        content = (message.content or "").strip()
        if not sender_openid or not content:
            return
        await route_c2c_inbound(
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            app_id=self._app_id,
            owner_openid=self._owner_openid,
            sender_openid=sender_openid,
            content=content,
            msg_id=message.id or "",
            reply_sender=self._sender,
        )


def build_qq_client_for_gateway(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    app_id: str,
    secret: str,
    owner_openid: str,
) -> QQOfficialClient:
    """Construct a configured botpy client ready for ``client.start(app_id, secret)``."""
    intents = botpy.Intents(public_messages=True)
    client = QQOfficialClient(intents=intents, bot_log=False, timeout=20)
    client.configure(
        tenant_id=tenant_id,
        user_id=user_id,
        app_id=app_id,
        secret=secret,
        owner_openid=owner_openid,
    )
    return client
