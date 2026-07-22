"""QQ official bot QR one-click bind (ADR-028) — pure-Python port of the official
``@tencent-connect/qqbot-connector`` flow (endpoints verified against that SDK).

No partner token is needed — ``create_bind_task`` only sends a client-generated
base64 AES-256 key. The scan page shows "第三方机器人" when ``source`` is empty.

Flow:
1. :func:`create_bind_task` — ``POST {host}/lite/create_bind_task {"key": <b64 32>}``
   → ``task_id`` (+ the key we generated).
2. :func:`connect_url` — the QR target the user scans with mobile QQ.
3. :func:`poll_bind_result` — ``POST {host}/lite/poll_bind_result {"task_id"}`` →
   status; on completed, decrypt ``bot_encrypt_secret`` (AES-256-GCM under the key)
   to the AppSecret and return ``bot_appid`` + ``user_openid`` (the scanning owner).
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROD_HOST = "q.qq.com"

STATUS_NONE = 0
STATUS_PENDING = 1
STATUS_COMPLETED = 2
STATUS_EXPIRED = 3


class QQBindError(Exception):
    """A QQ bind API call failed or returned a malformed payload."""


@dataclass(frozen=True)
class BindTask:
    task_id: str
    key: str  # base64 AES-256 key we generated


@dataclass(frozen=True)
class BindResult:
    status: int
    app_id: str = ""
    secret: str = ""
    owner_openid: str = ""


def generate_bind_key() -> str:
    """A fresh base64-encoded 32-byte (AES-256) bind key."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def connect_url(task_id: str, source: str = "", host: str = PROD_HOST) -> str:
    """The QR target scanned with mobile QQ (``source`` empty → "第三方机器人")."""
    return (
        f"https://{host}/qqbot/openclaw/connect.html"
        f"?task_id={quote(task_id, safe='')}&source={quote(source, safe='')}&_wv=2"
    )


def decrypt_secret(encrypted_b64: str, key_b64: str) -> str:
    """AES-256-GCM decrypt ``bot_encrypt_secret`` (12B nonce + ciphertext + 16B tag)."""
    try:
        key = base64.b64decode(key_b64)
        raw = base64.b64decode(encrypted_b64)
    except Exception as exc:  # noqa: BLE001 - normalized
        raise QQBindError("malformed bind credential encoding") from exc
    if len(key) != 32 or len(raw) <= 28:
        raise QQBindError("malformed bind credential payload")
    nonce, ct_and_tag = raw[:12], raw[12:]
    try:
        return AESGCM(key).decrypt(nonce, ct_and_tag, None).decode("utf-8")
    except InvalidTag as exc:
        raise QQBindError("bind credential decryption failed") from exc


async def _post(
    host: str, path: str, payload: dict[str, str], timeout_s: float
) -> dict[str, object]:
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            resp = await http.post(
                f"https://{host}{path}", json=payload, headers={"Accept": "application/json"}
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise QQBindError(f"QQ bind request failed: {exc}") from exc
    if not isinstance(data, dict):
        raise QQBindError("QQ bind response is not an object")
    retcode = data.get("retcode")
    if retcode not in (None, 0):
        raise QQBindError(str(data.get("msg") or "QQ bind request returned an error"))
    return data


async def create_bind_task(host: str = PROD_HOST, timeout_s: float = 10.0) -> BindTask:
    key = generate_bind_key()
    data = await _post(host, "/lite/create_bind_task", {"key": key}, timeout_s)
    payload = data.get("data")
    task_id = str(payload.get("task_id", "")) if isinstance(payload, dict) else ""
    if not task_id:
        raise QQBindError("QQ bind task response missing task_id")
    return BindTask(task_id=task_id, key=key)


async def poll_bind_result(
    task_id: str, key: str, host: str = PROD_HOST, timeout_s: float = 10.0
) -> BindResult:
    data = await _post(host, "/lite/poll_bind_result", {"task_id": task_id}, timeout_s)
    payload = data.get("data")
    if not isinstance(payload, dict):
        return BindResult(status=STATUS_NONE)
    try:
        status = int(payload.get("status", STATUS_NONE))
    except (TypeError, ValueError):
        status = STATUS_NONE
    if status != STATUS_COMPLETED:
        return BindResult(status=status)
    app_id = str(payload.get("bot_appid") or "").strip()
    encrypted = str(payload.get("bot_encrypt_secret") or "").strip()
    owner_openid = str(payload.get("user_openid") or "").strip()
    if not app_id or not encrypted:
        raise QQBindError("scan completed but the QQ bot credential was incomplete")
    return BindResult(
        status=status,
        app_id=app_id,
        secret=decrypt_secret(encrypted, key),
        owner_openid=owner_openid,
    )
