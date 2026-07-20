"""Gmail OAuth + read API client (docs/06).

The real client talks to Google's OAuth + Gmail endpoints; fakes (same Protocols)
are injected in tests so the connect/callback + sync round-trips never hit the
network. OAuth/profile/message HTTP bodies are never logged (config §3.5).
"""

from __future__ import annotations

import datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.config import settings

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_PROFILE_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
_MESSAGES_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


class GmailOAuthClient(Protocol):
    def authorization_url(self, *, state: str, challenge: str) -> str: ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, object]: ...

    async def fetch_email(self, *, access_token: str) -> str: ...


class GmailSyncClient(Protocol):
    async def refresh(self, *, refresh_token: str) -> dict[str, object]: ...

    async def list_message_ids(
        self, *, access_token: str, query: str, max_results: int = 100
    ) -> list[str]: ...

    async def get_message(self, *, access_token: str, message_id: str) -> dict[str, object]: ...


def _parse_message(raw: dict[str, Any]) -> dict[str, object]:
    """Normalize a Gmail message resource into the connector-item content shape."""
    headers = {
        str(h.get("name", "")).lower(): h.get("value", "")
        for h in (raw.get("payload", {}) or {}).get("headers", [])
    }
    internal_ms = int(raw.get("internalDate", "0") or "0")
    return {
        "id": str(raw.get("id", "")),
        "thread_id": raw.get("threadId"),
        "history_id": str(raw.get("historyId")) if raw.get("historyId") is not None else None,
        "internal_date": datetime.datetime.fromtimestamp(internal_ms / 1000, tz=datetime.UTC),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": raw.get("snippet", ""),
        "label_ids": list(raw.get("labelIds", [])),
    }


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

    async def refresh(self, *, refresh_token: str) -> dict[str, object]:
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(_TOKEN_ENDPOINT, data=data)
            resp.raise_for_status()
            result: dict[str, object] = resp.json()
            return result

    async def list_message_ids(
        self, *, access_token: str, query: str, max_results: int = 100
    ) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while len(ids) < max_results:
                params: dict[str, str | int] = {"q": query, "maxResults": min(100, max_results)}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(_MESSAGES_ENDPOINT, params=params, headers=headers)
                resp.raise_for_status()
                body = resp.json()
                ids.extend(str(m["id"]) for m in body.get("messages", []))
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
        return ids[:max_results]

    async def get_message(self, *, access_token: str, message_id: str) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {access_token}"}
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "Subject"),
            ("metadataHeaders", "Date"),
        ]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{_MESSAGES_ENDPOINT}/{message_id}", params=params, headers=headers
            )
            resp.raise_for_status()
            return _parse_message(resp.json())


def get_gmail_client() -> GmailOAuthClient:
    """FastAPI dependency: the configured real client (overridden in tests)."""
    return GoogleGmailOAuthClient(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        redirect_uri=settings.gmail_redirect,
        scope=settings.gmail_scope,
    )


def build_gmail_sync_client() -> GmailSyncClient:
    """The configured real Gmail read client (used by the sync worker job)."""
    return GoogleGmailOAuthClient(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        redirect_uri=settings.gmail_redirect,
        scope=settings.gmail_scope,
    )
