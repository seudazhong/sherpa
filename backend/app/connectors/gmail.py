"""Gmail OAuth client: authorization URL, code exchange, account email.

The real client talks to Google's OAuth + Gmail profile endpoints; a fake client
(same Protocol) is injected in tests so the connect/callback round-trip never
hits the network. OAuth/profile HTTP bodies are never logged (config §3.5).
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.config import settings

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_PROFILE_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


class GmailOAuthClient(Protocol):
    def authorization_url(self, *, state: str, challenge: str) -> str: ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, object]: ...

    async def fetch_email(self, *, access_token: str) -> str: ...


class GoogleGmailOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: str,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scope = scope
        self._timeout = timeout

    def authorization_url(self, *, state: str, challenge: str) -> str:
        params = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": self._scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
        return f"{_AUTH_ENDPOINT}?{params}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, object]:
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(_TOKEN_ENDPOINT, data=data)
            resp.raise_for_status()
            result: dict[str, object] = resp.json()
            return result

    async def fetch_email(self, *, access_token: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                _PROFILE_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
            resp.raise_for_status()
            return str(resp.json()["emailAddress"])


def get_gmail_client() -> GmailOAuthClient:
    """FastAPI dependency: the configured real client (overridden in tests)."""
    return GoogleGmailOAuthClient(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        redirect_uri=settings.gmail_redirect,
        scope=settings.gmail_scope,
    )
