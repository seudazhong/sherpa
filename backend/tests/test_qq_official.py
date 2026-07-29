"""Official QQ bot tests (ADR-028): secret seal, QR bind, sender, routing, config REST.

Unit tests never touch the network (httpx/botpy are stubbed). API tests skip
without Postgres+Redis; the QR bind + test-connection are monkeypatched so no real
QQ endpoint is called.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.channels.qq import RecordingQQClient
from app.channels.qq_bind import (
    BindResult,
    QQBindError,
    connect_url,
    decrypt_secret,
    generate_bind_key,
    poll_bind_result,
)
from app.channels.qq_official import (
    QQOfficialSender,
    build_qq_sender,
    route_c2c_inbound,
)
from app.config import settings
from app.db import ping_db
from app.main import app
from app.redis_client import ping_redis
from app.security.channel_secret import (
    ChannelSecretIdentity,
    open_channel_secret,
    seal_channel_secret,
)
from app.security.keyring import load_keyring
from app.security.vault import CredentialIntegrityError, connector_vault_capability
from tests.db_guard import drop_owner_tenant

# --------------------------------------------------------------------------- #
# Unit — secret seal + QR bind (no I/O).                                       #
# --------------------------------------------------------------------------- #


def test_channel_secret_roundtrip() -> None:
    ident = ChannelSecretIdentity(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), channel="qq")
    keyring = load_keyring()
    seal = seal_channel_secret("app-secret-123", ident, keyring)
    assert seal.secret_enc and seal.nonce
    opened = open_channel_secret(seal, ident, connector_vault_capability(), keyring)
    assert opened == "app-secret-123"
    # Wrong identity (different channel) fails the AEAD auth.
    other = ChannelSecretIdentity(tenant_id=ident.tenant_id, user_id=ident.user_id, channel="x")
    with pytest.raises(CredentialIntegrityError):
        open_channel_secret(seal, other, connector_vault_capability(), keyring)


def test_qq_bind_decrypt_roundtrip() -> None:
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key_b64 = generate_bind_key()
    key = base64.b64decode(key_b64)
    nonce = os.urandom(12)
    ct_and_tag = AESGCM(key).encrypt(nonce, b"the-real-secret", None)
    payload = base64.b64encode(nonce + ct_and_tag).decode()
    assert decrypt_secret(payload, key_b64) == "the-real-secret"
    with pytest.raises(QQBindError):
        decrypt_secret("not-base64-!!!", key_b64)


def test_qq_connect_url() -> None:
    url = connect_url("task-99", "Sherpa")
    assert "q.qq.com/qqbot/openclaw/connect.html" in url
    assert "task_id=task-99" in url and "source=Sherpa" in url
    assert "source=&" in connect_url("t", "")


@pytest.mark.asyncio
async def test_poll_bind_result_states(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key_b64 = generate_bind_key()

    async def _fake_post(host: str, path: str, payload: dict[str, str], t: float) -> dict[str, Any]:
        return _fake_post.resp  # type: ignore[attr-defined]

    monkeypatch.setattr("app.channels.qq_bind._post", _fake_post)

    _fake_post.resp = {"retcode": 0, "data": {"status": 1}}  # type: ignore[attr-defined]
    assert (await poll_bind_result("t", key_b64)).status == 1

    key = base64.b64decode(key_b64)
    nonce = os.urandom(12)
    enc = base64.b64encode(nonce + AESGCM(key).encrypt(nonce, b"secret-xyz", None)).decode()
    _fake_post.resp = {  # type: ignore[attr-defined]
        "retcode": 0,
        "data": {
            "status": 2,
            "bot_appid": "102000001",
            "bot_encrypt_secret": enc,
            "user_openid": "owner_openid_1",
        },
    }
    done: BindResult = await poll_bind_result("t", key_b64)
    assert done.status == 2 and done.app_id == "102000001"
    assert done.secret == "secret-xyz" and done.owner_openid == "owner_openid_1"


# --------------------------------------------------------------------------- #
# Unit — sender + inbound routing (no I/O).                                    #
# --------------------------------------------------------------------------- #


def test_build_qq_sender_selection() -> None:
    assert isinstance(build_qq_sender("", ""), RecordingQQClient)
    assert isinstance(build_qq_sender("appid", "secret"), QQOfficialSender)


@pytest.mark.asyncio
async def test_qq_sender_passive_reply() -> None:
    sender = QQOfficialSender("appid", "secret")

    class _FakeAPI:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def post_c2c_message(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"id": "m1"}

    fake = _FakeAPI()
    sender._api = fake  # type: ignore[assignment]  # skip login
    ok = await sender.send_private("openid_1", "hello", "msg_9")
    assert ok
    assert fake.calls[0]["openid"] == "openid_1"
    assert fake.calls[0]["msg_id"] == "msg_9"
    assert fake.calls[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_route_c2c_owner_allowlist_blocks() -> None:
    result = await route_c2c_inbound(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        app_id="appid",
        owner_openid="the_owner",
        sender_openid="a_stranger",
        content="hi",
        msg_id="m1",
        reply_sender=RecordingQQClient(),
    )
    assert result["status"] == "sender_not_allowed"


# --------------------------------------------------------------------------- #
# API — config PUT/status, test-connection, QR bind (skip without DB+Redis).   #
# --------------------------------------------------------------------------- #


async def _drop_owner() -> None:
    await drop_owner_tenant()


async def _login(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": settings.owner_email, "password": settings.owner_password},
    )
    assert r.status_code == 200
    return str(r.json()["csrf_token"])


@pytest.mark.asyncio
async def test_qq_config_put_status_and_test(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    async def _fake_test(app_id: str, secret: str) -> tuple[bool, str]:
        return True, "sherpa-bot"

    monkeypatch.setattr("app.channels.qq_official.test_qq_credentials", _fake_test)

    await _drop_owner()
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            csrf = await _login(client)

            r = await client.put(
                "/channels/qq/config",
                json={
                    "app_id": "102000001",
                    "enabled": True,
                    "owner_openid": "owner_1",
                    "secret": "the-secret",
                },
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["configured"] and body["app_id"] == "102000001" and body["secret_set"]

            # status projection reflects it
            r = await client.get("/channels")
            assert r.json()["qq"]["configured"]

            # test-connection uses stored creds (secret never leaves the server)
            r = await client.post("/channels/qq/test", headers={"X-CSRF-Token": csrf})
            assert r.status_code == 200 and r.json()["ok"]

            # updating without a secret keeps the stored one
            r = await client.put(
                "/channels/qq/config",
                json={"app_id": "102000002", "enabled": True, "owner_openid": "", "secret": ""},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200 and r.json()["secret_set"]
    finally:
        await _drop_owner()


@pytest.mark.asyncio
async def test_qq_bind_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    from app.channels import qq_bind

    async def _fake_create(host: str = "q.qq.com", timeout_s: float = 10.0) -> qq_bind.BindTask:
        return qq_bind.BindTask(task_id="task-1", key=generate_bind_key())

    states = iter(
        [
            qq_bind.BindResult(status=1),
            qq_bind.BindResult(
                status=2, app_id="102000009", secret="scanned-secret", owner_openid="scanner_1"
            ),
        ]
    )

    async def _fake_poll(
        task_id: str, key: str, host: str = "q.qq.com", timeout_s: float = 10.0
    ) -> qq_bind.BindResult:
        return next(states)

    monkeypatch.setattr("app.channels.qq_bind.create_bind_task", _fake_create)
    monkeypatch.setattr("app.channels.qq_bind.poll_bind_result", _fake_poll)

    await _drop_owner()
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            csrf = await _login(client)

            r = await client.post("/channels/qq/bind/start", headers={"X-CSRF-Token": csrf})
            assert r.status_code == 200
            task_id = r.json()["task_id"]
            assert "connect.html" in r.json()["qr_url"]

            r = await client.post(
                "/channels/qq/bind/poll",
                json={"task_id": task_id},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200 and r.json()["status"] == "pending"

            r = await client.post(
                "/channels/qq/bind/poll",
                json={"task_id": task_id},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200
            done = r.json()
            assert done["status"] == "completed" and done["app_id"] == "102000009"

            # the bound bot is now the active config
            r = await client.get("/channels")
            assert r.json()["qq"]["configured"] and r.json()["qq"]["app_id"] == "102000009"
    finally:
        await _drop_owner()
