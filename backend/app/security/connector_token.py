"""Direct AES-256-GCM connector-token sealing (contracts/data-model.md connectors).

The connectors table stores token_enc/nonce/kek_id/key_version/token_algorithm/
aad_version (no wrapped-DEK columns), so connector OAuth tokens are sealed
DIRECTLY under the active KEK with AAD recomputed from row identity. This is the
v1 connector variant of the credential vault (config-and-secrets §3); the
DEK-per-record envelope in vault.py applies where an encrypted_dek is stored.
Decrypt is gated behind the same connector-vault capability.
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
class ConnectorTokenIdentity:
    tenant_id: uuid.UUID
    connector_id: uuid.UUID
    external_account_id: str
    kind: str = "gmail"


@dataclasses.dataclass(frozen=True)
class ConnectorSeal:
    token_enc: bytes
    nonce: bytes
    kek_id: str
    key_version: int
    token_algorithm: str
    aad_version: int


def _aad(identity: ConnectorTokenIdentity) -> bytes:
    return json.dumps(
        {
            "aad_version": AAD_VERSION,
            "connector_id": str(identity.connector_id),
            "external_account_id": identity.external_account_id,
            "kind": identity.kind,
            "tenant_id": str(identity.tenant_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_connector_token(
    token_json: dict[str, object], identity: ConnectorTokenIdentity, keyring: Keyring
) -> ConnectorSeal:
    """Seal an OAuth token dict directly under the active KEK."""
    active = keyring.active
    nonce = os.urandom(12)
    plaintext = json.dumps(token_json, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(active.key).encrypt(nonce, plaintext, _aad(identity))
    return ConnectorSeal(
        token_enc=ciphertext,
        nonce=nonce,
        kek_id=active.id,
        key_version=active.version,
        token_algorithm=ALGORITHM,
        aad_version=AAD_VERSION,
    )


def open_connector_token(
    seal: ConnectorSeal,
    identity: ConnectorTokenIdentity,
    capability: ConnectorCapability,
    keyring: Keyring,
) -> dict[str, object]:
    """Recompute AAD from identity and decrypt the token under its KEK."""
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
    if not isinstance(parsed, dict):
        raise CredentialIntegrityError("token payload is not a JSON object")
    return parsed
