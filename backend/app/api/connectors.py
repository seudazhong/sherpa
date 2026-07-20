"""Gmail connector endpoints (api.md §4.4, §3.4): connect / callback / list / disconnect.

connect issues a PKCE authorization URL; the callback validates state+PKCE,
exchanges the code, verifies the read-only scope, seals the token under the KEK
(AEAD), and commits the connector before redirecting. Disconnect revokes the
local credential. Plaintext tokens never appear in a response, event, or log.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    Connector as ConnectorSchema,
)
from app.api.schemas import (
    ConnectorSyncStatus,
    GmailConnectRequest,
    GmailSyncScope,
    OAuthStart,
)
from app.auth import RequestContext, require_context, require_csrf
from app.config import settings
from app.connectors.gmail import GmailOAuthClient, get_gmail_client
from app.connectors.oauth_state import (
    OAuthState,
    code_challenge,
    consume_state,
    create_state,
    new_code_verifier,
)
from app.db import get_session
from app.models import Connector
from app.security import ConnectorTokenIdentity, load_keyring, seal_connector_token

router = APIRouter(tags=["connectors"])

_NULL_TOKEN = {
    "token_enc": None,
    "nonce": None,
    "kek_id": None,
    "key_version": None,
    "token_algorithm": None,
    "aad_version": None,
}


def _result_url(return_to: str, result: str, code: str | None) -> str:
    sep = "&" if "?" in return_to else "?"
    query = f"gmail={result}" + (f"&code={code}" if code else "")
    return f"{return_to}{sep}{query}"


def _to_schema(row: Connector) -> ConnectorSchema:
    return ConnectorSchema(
        id=row.id,
        tenant_id=row.tenant_id,
        kind="gmail",
        status=row.status,  # type: ignore[arg-type]
        account_email=row.external_account_id,
        granted_scopes=list(row.scopes),
        sync_scope=GmailSyncScope.model_validate(row.cursor.get("sync_scope") or {}),
        sync=ConnectorSyncStatus(
            cursor_present=bool(row.cursor.get("history_id")),
            last_started_at=None,
            last_succeeded_at=row.last_sync_at,
            last_error_code=None,
            last_run_id=None,
        ),
        version=row.refresh_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/connectors/gmail/connect")
async def gmail_connect(
    body: GmailConnectRequest,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    client: Annotated[GmailOAuthClient, Depends(get_gmail_client)],
) -> OAuthStart:
    verifier = new_code_verifier()
    state_param = await create_state(
        OAuthState(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            connector_id=uuid.uuid4(),
            code_verifier=verifier,
            return_to=body.return_to,
            sync_scope=body.sync_scope.model_dump(),
        )
    )
    url = client.authorization_url(state=state_param, challenge=code_challenge(verifier))
    expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        seconds=settings.oauth_state_ttl_seconds
    )
    return OAuthStart(authorization_url=url, expires_at=expires_at)


@router.get("/connectors/gmail/oauth/callback")
async def gmail_callback(
    db: Annotated[AsyncSession, Depends(get_session)],
    client: Annotated[GmailOAuthClient, Depends(get_gmail_client)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    st = await consume_state(state)
    if st is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_state")
    return_to = st.return_to

    if error or not code:
        return RedirectResponse(
            _result_url(return_to, "failed", error or "no_code"), status_code=303
        )
    try:
        tokens = await client.exchange_code(code=code, code_verifier=st.code_verifier)
    except Exception:
        return RedirectResponse(
            _result_url(return_to, "failed", "exchange_failed"), status_code=303
        )

    granted = str(tokens.get("scope", "")).split()
    if settings.gmail_scope not in granted:
        return RedirectResponse(_result_url(return_to, "failed", "scope_mismatch"), status_code=303)

    email = await client.fetch_email(access_token=str(tokens.get("access_token", "")))
    identity = ConnectorTokenIdentity(
        tenant_id=st.tenant_id, connector_id=st.connector_id, external_account_id=email
    )
    token_json: dict[str, object] = {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "scope": tokens.get("scope"),
        "token_type": tokens.get("token_type"),
        "expires_in": tokens.get("expires_in"),
    }
    seal = seal_connector_token(token_json, identity, load_keyring())

    db.add(
        Connector(
            tenant_id=st.tenant_id,
            id=st.connector_id,
            user_id=st.user_id,
            kind="gmail",
            channel_installation_id=f"gmail:{email}",
            external_account_id=email,
            token_enc=seal.token_enc,
            nonce=seal.nonce,
            kek_id=seal.kek_id,
            key_version=seal.key_version,
            token_algorithm=seal.token_algorithm,
            aad_version=seal.aad_version,
            scopes=granted,
            status="active",
            cursor={"sync_scope": st.sync_scope},
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            _result_url(return_to, "failed", "already_connected"), status_code=303
        )
    return RedirectResponse(_result_url(return_to, "connected", None), status_code=303)


@router.get("/connectors")
async def list_connectors(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ConnectorSchema]:
    rows = (
        (
            await db.execute(
                select(Connector)
                .where(Connector.tenant_id == ctx.tenant_id)
                .order_by(Connector.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_schema(r) for r in rows]


@router.delete("/connectors/{connector_id}")
async def disconnect_connector(
    connector_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectorSchema:
    row = await db.get(Connector, (ctx.tenant_id, connector_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connector not found")
    # v1: revoke the local credential synchronously (null token columns). Async
    # provider-side revocation via the effect path lands with the connector worker.
    row.status = "revoked"
    for col, val in _NULL_TOKEN.items():
        setattr(row, col, val)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    await db.commit()
    return _to_schema(row)
