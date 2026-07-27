"""Direct AES-256-GCM GitHub-connection-token sealing (ADR-019/038; data-model
``github_connections``).

W2b stores a GitHub credential in ``github_connections`` reusing the connectors AEAD
column shape (``token_enc/nonce/kek_id/key_version/token_algorithm/aad_version``). The
**first version accepts ONLY a fine-grained PAT** (``github_pat_`` prefix, with
``contents:read``); classic PAT / OAuth / GitHub App installation tokens are rejected at
the input boundary. GitHub App installation tokens remain a *forward* ``auth_kind`` (not
built yet), so the schema keeps the column extensible without widening what v1 accepts.
The token is sealed DIRECTLY under the active KEK with AAD recomputed from row identity
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

# Fine-grained PAT prefix — the ONLY GitHub credential shape v1 accepts (ADR-038).
FINE_GRAINED_PAT_PREFIX = "github_pat_"

# Documented GitHub credential type prefixes, most-specific first (``github_pat_`` must be
# matched before any shorter ``gh*_`` prefix). Used ONLY to derive a non-sensitive category
# label for gating/reporting — classification never returns the token, its length, any
# fragment, or a hash.
_GITHUB_TOKEN_PREFIXES: tuple[tuple[str, str], ...] = (
    (FINE_GRAINED_PAT_PREFIX, "fine_grained_pat"),
    ("ghp_", "classic_pat"),
    ("gho_", "oauth"),
    ("ghu_", "app_user_to_server"),
    ("ghs_", "app_installation"),
    ("ghr_", "refresh"),
)


def classify_github_token(token: str) -> str:
    """Return a stable, non-sensitive category label for a GitHub credential.

    Categories mirror GitHub's documented token type prefixes (``fine_grained_pat`` /
    ``classic_pat`` / ``oauth`` / ``app_user_to_server`` / ``app_installation`` /
    ``refresh``), falling back to ``other``. This is a pure classifier: it inspects only
    the leading prefix and NEVER discloses the token, its length, any fragment, or a hash.
    Safe to log/report (the label alone carries no secret material).
    """
    stripped = (token or "").strip()
    for prefix, label in _GITHUB_TOKEN_PREFIXES:
        if stripped.startswith(prefix):
            return label
    return "other"


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
