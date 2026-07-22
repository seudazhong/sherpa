"""Direct AES-256-GCM sealing for channel secrets (ADR-028; QQ AppSecret, etc.).

The `channel_configs` table stores `secret_enc`/`secret_nonce`/`kek_id`/
`key_version`, so a channel secret (e.g. the QQ official bot AppSecret) is sealed
DIRECTLY under the active KEK with AAD recomputed from row identity — the same
crypto shape as the connector token seal (`connector_token.py`), reused so all
credential decryption stays inside `app/security`. Decrypt is capability-gated
(the connector-vault capability) so no generic route/tool can reach plaintext.
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
class ChannelSecretIdentity:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    channel: str


@dataclasses.dataclass(frozen=True)
class ChannelSeal:
    secret_enc: bytes
    nonce: bytes
    kek_id: str
    key_version: int
    algorithm: str
    aad_version: int


def _aad(identity: ChannelSecretIdentity) -> bytes:
    return json.dumps(
        {
            "aad_version": AAD_VERSION,
            "channel": identity.channel,
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_channel_secret(
    secret: str, identity: ChannelSecretIdentity, keyring: Keyring
) -> ChannelSeal:
    """Seal a channel secret string directly under the active KEK."""
    active = keyring.active
    nonce = os.urandom(12)
    ciphertext = AESGCM(active.key).encrypt(nonce, secret.encode("utf-8"), _aad(identity))
    return ChannelSeal(
        secret_enc=ciphertext,
        nonce=nonce,
        kek_id=active.id,
        key_version=active.version,
        algorithm=ALGORITHM,
        aad_version=AAD_VERSION,
    )


def open_channel_secret(
    seal: ChannelSeal,
    identity: ChannelSecretIdentity,
    capability: ConnectorCapability,
    keyring: Keyring,
) -> str:
    """Recompute AAD from identity and decrypt the channel secret under its KEK."""
    _require_capability(capability)
    try:
        kek = keyring.require(seal.kek_id, seal.key_version)
    except KeyringError as exc:
        raise CredentialIntegrityError(str(exc)) from exc
    try:
        plaintext = AESGCM(kek).decrypt(seal.nonce, seal.secret_enc, _aad(identity))
    except InvalidTag as exc:
        raise CredentialIntegrityError("AES-GCM authentication failed") from exc
    return plaintext.decode("utf-8")
