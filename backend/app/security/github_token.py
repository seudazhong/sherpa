"""Direct AES-256-GCM GitHub-connection-token sealing (ADR-019/038; data-model
``github_connections``).

W2b stores a GitHub credential (a fine-grained PAT with ``contents:read``, or a
GitHub App installation token) in ``github_connections`` reusing the connectors AEAD
column shape (``token_enc/nonce/kek_id/key_version/token_algorithm/aad_version``). The
token is sealed DIRECTLY under the active KEK with AAD recomputed from row identity
(mirrors :mod:`app.security.connector_token`). Decrypt is gated behind the same
connector-vault capability and happens ONLY at the import-worker/connector boundary;
the plaintext token never enters a project tree, snapshot, prompt, log, event journal,
tool result, or (W3) sandbox.
"""

from __future__ import annotations

import dataclasses
import json
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.security.keyring import Keyring, KeyringError
from app.security.vault import (
    ConnectorCapability,
    CredentialIntegrityError,
    _require_capability,
)

ALGORITHM = "AES-256-GCM"
AAD_VERSION = 1


@dataclasses.dataclass(frozen=True)
class GithubTokenIdentity:
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    user_id: uuid.UUID
    auth_kind: str = "pat"


@dataclasses.dataclass(frozen=True)
class GithubSeal:
    token_enc: bytes
    nonce: bytes
    kek_id: str
    key_version: int
    token_algorithm: str
    aad_version: int


def _aad(identity: GithubTokenIdentity) -> bytes:
    return json.dumps(
        {
            "aad_version": AAD_VERSION,
            "auth_kind": identity.auth_kind,
            "connection_id": str(identity.connection_id),
            "provider": "github",
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_github_token(token: str, identity: GithubTokenIdentity, keyring: Keyring) -> GithubSeal:
    """Seal a GitHub token string directly under the active KEK."""
    active = keyring.active
    nonce = os.urandom(12)
    plaintext = json.dumps({"token": token}, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(active.key).encrypt(nonce, plaintext, _aad(identity))
    return GithubSeal(
        token_enc=ciphertext,
        nonce=nonce,
        kek_id=active.id,
        key_version=active.version,
        token_algorithm=ALGORITHM,
        aad_version=AAD_VERSION,
    )


def open_github_token(
    seal: GithubSeal,
    identity: GithubTokenIdentity,
    capability: ConnectorCapability,
    keyring: Keyring,
) -> str:
    """Recompute AAD from identity and decrypt the GitHub token under its KEK.

    Requires the connector-vault capability so no generic route/tool can reach the
    plaintext. AES-GCM auth failure is terminal (never returns partial plaintext).
    """
    _require_capability(capability)
    try:
        kek = keyring.require(seal.kek_id, seal.key_version)
    except KeyringError as exc:
        raise CredentialIntegrityError(str(exc)) from exc
    try:
        plaintext = AESGCM(kek).decrypt(seal.nonce, seal.token_enc, _aad(identity))
    except InvalidTag as exc:
        raise CredentialIntegrityError("AES-GCM authentication failed") from exc
    parsed = json.loads(plaintext)
    if not isinstance(parsed, dict) or "token" not in parsed:
        raise CredentialIntegrityError("github token payload malformed")
    token = parsed["token"]
    if not isinstance(token, str):
        raise CredentialIntegrityError("github token payload malformed")
    return token
