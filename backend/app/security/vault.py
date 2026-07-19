"""Connector credential vault: AES-256-GCM DEK-per-record envelope (config §3.2-3.4).

This is the ONLY module that decrypts stored credentials. Each record gets a
random 256-bit DEK; the credential JSON is sealed with AES-256-GCM under that DEK
and canonical AAD binding immutable identity (tenant/connector/credential/kind).
The DEK is wrapped by the active KEK (its own wrap-AAD), so rotation rewraps DEKs
without ever touching OAuth plaintext. AES-GCM auth failure is a terminal
credential-integrity error — never return partial plaintext, never retry with a
different identity/AAD.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.security.keyring import Keyring, KeyringError

_ENVELOPE_VERSION = 1
_ALGORITHM = "AES-256-GCM"


class VaultError(Exception):
    """Base class for vault failures."""


class CredentialIntegrityError(VaultError):
    """AAD mismatch or AES-GCM authentication failure. Terminal; requires reconcile."""


class CapabilityError(VaultError):
    """Decrypt attempted without the connector-vault capability."""


# --- capability boundary -------------------------------------------------------
# `open_oauth_credential` requires a capability that only this module mints, so
# no generic route/tool/util can accidentally reach plaintext (config §3.1).
_CAP = object()


@dataclasses.dataclass(frozen=True)
class ConnectorCapability:
    _token: object


def connector_vault_capability() -> ConnectorCapability:
    """Mint the capability. Only connector execution paths should call this."""
    return ConnectorCapability(_CAP)


def _require_capability(capability: ConnectorCapability) -> None:
    if not isinstance(capability, ConnectorCapability) or capability._token is not _CAP:
        raise CapabilityError("connector-vault capability required")


# --- identity + envelope -------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class CredentialIdentity:
    credential_id: uuid.UUID
    connector_id: uuid.UUID
    tenant_id: uuid.UUID
    credential_kind: str = "gmail_oauth"


@dataclasses.dataclass(frozen=True)
class CredentialEnvelope:
    kek_id: str
    key_version: int
    nonce: bytes
    ciphertext: bytes
    aad: bytes
    encrypted_dek: bytes
    algorithm: str = _ALGORITHM
    aad_version: int = 1


def _canonical(obj: dict[str, object]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _credential_aad(identity: CredentialIdentity) -> bytes:
    return _canonical(
        {
            "aad_version": 1,
            "credential_id": str(identity.credential_id),
            "connector_id": str(identity.connector_id),
            "credential_kind": identity.credential_kind,
            "tenant_id": str(identity.tenant_id),
        }
    )


def _dek_wrap_aad(credential_id: uuid.UUID, kek_id: str, key_version: int) -> bytes:
    return _canonical(
        {
            "purpose": "sherpa-dek-wrap-v1",
            "credential_id": str(credential_id),
            "kek_id": kek_id,
            "key_version": key_version,
        }
    )


def _encode_v1(wrap_nonce: bytes, wrapped: bytes) -> bytes:
    return bytes([_ENVELOPE_VERSION]) + wrap_nonce + wrapped


def _decode_v1(blob: bytes) -> tuple[bytes, bytes]:
    if len(blob) < 13 or blob[0] != _ENVELOPE_VERSION:
        raise CredentialIntegrityError("malformed encrypted_dek")
    return blob[1:13], blob[13:]


def seal_oauth_credential(
    token_json: dict[str, object], identity: CredentialIdentity, keyring: Keyring
) -> CredentialEnvelope:
    """Seal credential JSON under a fresh DEK wrapped by the active KEK."""
    active = keyring.active
    aad = _credential_aad(identity)

    dek = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, _canonical(token_json), aad)

    wrap_aad = _dek_wrap_aad(identity.credential_id, active.id, active.version)
    wrap_nonce = os.urandom(12)
    wrapped = AESGCM(active.key).encrypt(wrap_nonce, dek, wrap_aad)

    return CredentialEnvelope(
        kek_id=active.id,
        key_version=active.version,
        nonce=nonce,
        ciphertext=ciphertext,
        aad=aad,
        encrypted_dek=_encode_v1(wrap_nonce, wrapped),
    )


def open_oauth_credential(
    envelope: CredentialEnvelope,
    identity: CredentialIdentity,
    capability: ConnectorCapability,
    keyring: Keyring,
) -> dict[str, object]:
    """Recompute AAD from identity, unwrap the DEK, and decrypt the payload."""
    _require_capability(capability)

    expected_aad = _credential_aad(identity)
    if not hmac.compare_digest(envelope.aad, expected_aad):
        raise CredentialIntegrityError("credential AAD mismatch")

    try:
        kek = keyring.require(envelope.kek_id, envelope.key_version)
    except KeyringError as exc:
        raise CredentialIntegrityError(str(exc)) from exc

    wrap_nonce, wrapped = _decode_v1(envelope.encrypted_dek)
    wrap_aad = _dek_wrap_aad(identity.credential_id, envelope.kek_id, envelope.key_version)
    try:
        dek = AESGCM(kek).decrypt(wrap_nonce, wrapped, wrap_aad)
        plaintext = AESGCM(dek).decrypt(envelope.nonce, envelope.ciphertext, expected_aad)
    except InvalidTag as exc:
        raise CredentialIntegrityError("AES-GCM authentication failed") from exc

    parsed = json.loads(plaintext)
    if not isinstance(parsed, dict):
        raise CredentialIntegrityError("credential payload is not a JSON object")
    return parsed


def rewrap_dek(
    envelope: CredentialEnvelope, identity: CredentialIdentity, keyring: Keyring
) -> CredentialEnvelope:
    """KEK rotation: unwrap the DEK with its old KEK and rewrap with the active KEK.

    Never decrypts the OAuth payload; only the wrapped DEK + key metadata change.
    """
    try:
        old_kek = keyring.require(envelope.kek_id, envelope.key_version)
    except KeyringError as exc:
        raise CredentialIntegrityError(str(exc)) from exc

    wrap_nonce, wrapped = _decode_v1(envelope.encrypted_dek)
    old_wrap_aad = _dek_wrap_aad(identity.credential_id, envelope.kek_id, envelope.key_version)
    try:
        dek = AESGCM(old_kek).decrypt(wrap_nonce, wrapped, old_wrap_aad)
    except InvalidTag as exc:
        raise CredentialIntegrityError("AES-GCM authentication failed") from exc

    active = keyring.active
    new_wrap_aad = _dek_wrap_aad(identity.credential_id, active.id, active.version)
    new_nonce = os.urandom(12)
    new_wrapped = AESGCM(active.key).encrypt(new_nonce, dek, new_wrap_aad)

    return dataclasses.replace(
        envelope,
        kek_id=active.id,
        key_version=active.version,
        encrypted_dek=_encode_v1(new_nonce, new_wrapped),
    )
