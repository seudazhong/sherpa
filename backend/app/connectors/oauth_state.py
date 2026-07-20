"""OAuth state + PKCE for the Gmail connect flow (api.md §4.4).

The `state` URL parameter is a random id signed with APP_SECRET; the sensitive
bits (PKCE code_verifier, tenant/user, connector id, return_to, requested
scope) live server-side in Redis under a short TTL and are single-use
(consumed with GETDEL). This closes CSRF/replay on the callback without putting
secrets in the URL.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import secrets
import uuid

from app.config import settings
from app.redis_client import client as redis

_PREFIX = "sherpa:v1:oauth:"


@dataclasses.dataclass(frozen=True)
class OAuthState:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    connector_id: uuid.UUID
    code_verifier: str
    return_to: str
    sync_scope: dict[str, object]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_code_verifier() -> str:
    return _b64url(secrets.token_bytes(32))


def code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _sig(sid: str) -> str:
    return hmac.new(settings.app_secret.encode(), sid.encode(), hashlib.sha256).hexdigest()


def _unsign(param: str) -> str | None:
    try:
        sid, sig = param.rsplit(".", 1)
    except ValueError:
        return None
    return sid if hmac.compare_digest(sig, _sig(sid)) else None


async def create_state(state: OAuthState) -> str:
    """Persist the state server-side and return the signed `state` URL param."""
    sid = secrets.token_urlsafe(32)
    payload = json.dumps(
        {
            "tenant_id": str(state.tenant_id),
            "user_id": str(state.user_id),
            "connector_id": str(state.connector_id),
            "code_verifier": state.code_verifier,
            "return_to": state.return_to,
            "sync_scope": state.sync_scope,
        },
        separators=(",", ":"),
    )
    await redis.set(_PREFIX + sid, payload, ex=settings.oauth_state_ttl_seconds)
    return f"{sid}.{_sig(sid)}"


async def consume_state(param: str | None) -> OAuthState | None:
    """Validate the signed param and atomically consume the server-side state."""
    if not param:
        return None
    sid = _unsign(param)
    if sid is None:
        return None
    raw = await redis.getdel(_PREFIX + sid)
    if raw is None:
        return None
    d = json.loads(raw)
    return OAuthState(
        tenant_id=uuid.UUID(d["tenant_id"]),
        user_id=uuid.UUID(d["user_id"]),
        connector_id=uuid.UUID(d["connector_id"]),
        code_verifier=d["code_verifier"],
        return_to=d["return_to"],
        sync_scope=d["sync_scope"],
    )
