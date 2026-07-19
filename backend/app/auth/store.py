"""Opaque server-side session store (Redis) with APP_SECRET-signed cookie.

The cookie carries only a signed opaque session id; all session state lives in
Redis under a TTL (`config §2`: seven-day default). APP_SECRET signs the cookie
(rotating it invalidates sessions); the CSRF token is stored in the session
record and echoed by unsafe requests as `X-CSRF-Token`.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import secrets
import uuid

from app.config import settings
from app.redis_client import client as redis

_PREFIX = "sherpa:v1:auth:"


@dataclasses.dataclass(frozen=True)
class SessionData:
    sid: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    csrf_token: str
    expires_at: datetime.datetime


def _sig(sid: str) -> str:
    return hmac.new(settings.app_secret.encode(), sid.encode(), hashlib.sha256).hexdigest()


def cookie_value(data: SessionData) -> str:
    """The signed cookie value: `<sid>.<hmac>`."""
    return f"{data.sid}.{_sig(data.sid)}"


def _unsign(cookie: str) -> str | None:
    try:
        sid, sig = cookie.rsplit(".", 1)
    except ValueError:
        return None
    return sid if hmac.compare_digest(sig, _sig(sid)) else None


def _serialize(data: SessionData) -> str:
    return json.dumps(
        {
            "user_id": str(data.user_id),
            "tenant_id": str(data.tenant_id),
            "email": data.email,
            "csrf_token": data.csrf_token,
            "expires_at": data.expires_at.isoformat(),
        },
        separators=(",", ":"),
    )


def _deserialize(sid: str, raw: str) -> SessionData:
    d = json.loads(raw)
    return SessionData(
        sid=sid,
        user_id=uuid.UUID(d["user_id"]),
        tenant_id=uuid.UUID(d["tenant_id"]),
        email=d["email"],
        csrf_token=d["csrf_token"],
        expires_at=datetime.datetime.fromisoformat(d["expires_at"]),
    )


async def create_session(*, user_id: uuid.UUID, tenant_id: uuid.UUID, email: str) -> SessionData:
    sid = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.UTC)
    data = SessionData(
        sid=sid,
        user_id=user_id,
        tenant_id=tenant_id,
        email=email,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now + datetime.timedelta(seconds=settings.session_ttl_seconds),
    )
    await redis.set(_PREFIX + sid, _serialize(data), ex=settings.session_ttl_seconds)
    return data


async def load_session(cookie: str | None) -> SessionData | None:
    if not cookie:
        return None
    sid = _unsign(cookie)
    if sid is None:
        return None
    raw = await redis.get(_PREFIX + sid)
    return _deserialize(sid, raw) if raw is not None else None


async def refresh_csrf(data: SessionData) -> SessionData:
    """Rotate the CSRF token, preserving the session and its remaining TTL."""
    rotated = dataclasses.replace(data, csrf_token=secrets.token_urlsafe(32))
    await redis.set(_PREFIX + data.sid, _serialize(rotated), keepttl=True)
    return rotated


async def delete_session(cookie: str | None) -> None:
    sid = _unsign(cookie) if cookie else None
    if sid is not None:
        await redis.delete(_PREFIX + sid)
