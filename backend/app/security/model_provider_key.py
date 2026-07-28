"""Direct AES-256-GCM model-provider-API-key sealing (ADR-041/019; data-model
``model_providers``).

A user-configured model source (OpenAI / Anthropic / Gemini / DeepSeek / Qwen / …) stores
its API key in ``model_providers`` reusing the connectors AEAD column shape
(``token_enc/nonce/kek_id/key_version/token_algorithm/aad_version``). The key is sealed
DIRECTLY under the active KEK with AAD recomputed from row identity (mirrors
:mod:`app.security.github_token`). Decrypt is gated behind the connector-vault capability
and happens ONLY at the ``Provider.stream()`` / test-connection boundary; the plaintext key
never enters a log, event journal, prompt, tool result, sandbox, or any REST response.
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
class ModelProviderKeyIdentity:
    tenant_id: uuid.UUID
    provider_id: uuid.UUID
    user_id: uuid.UUID
    kind: str  # openai_compatible | anthropic | gemini


@dataclasses.dataclass(frozen=True)
class ModelProviderSeal:
    token_enc: bytes
    nonce: bytes
    kek_id: str
    key_version: int
    token_algorithm: str
    aad_version: int


def _aad(identity: ModelProviderKeyIdentity) -> bytes:
    return json.dumps(
        {
            "aad_version": AAD_VERSION,
            "kind": identity.kind,
            "provider": "model_provider",
            "provider_id": str(identity.provider_id),
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_model_provider_key(
    key: str, identity: ModelProviderKeyIdentity, keyring: Keyring
) -> ModelProviderSeal:
    """Seal a provider API key string directly under the active KEK."""
    active = keyring.active
    nonce = os.urandom(12)
    plaintext = json.dumps({"key": key}, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(active.key).encrypt(nonce, plaintext, _aad(identity))
    return ModelProviderSeal(
        token_enc=ciphertext,
        nonce=nonce,
        kek_id=active.id,
        key_version=active.version,
        token_algorithm=ALGORITHM,
        aad_version=AAD_VERSION,
    )


def open_model_provider_key(
    seal: ModelProviderSeal,
    identity: ModelProviderKeyIdentity,
    capability: ConnectorCapability,
    keyring: Keyring,
) -> str:
    """Recompute AAD from identity and decrypt the provider key under its KEK.

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
    if not isinstance(parsed, dict) or "key" not in parsed:
        raise CredentialIntegrityError("model provider key payload malformed")
    key = parsed["key"]
    if not isinstance(key, str):
        raise CredentialIntegrityError("model provider key payload malformed")
    return key
