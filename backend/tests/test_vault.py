"""Credential vault (#11): AEAD seal/open, capability boundary, tamper detection,
KEK rotation rewrap, and canary/redaction checks. Pure crypto — no DB/Redis."""

from __future__ import annotations

import dataclasses
import os
import uuid

import pytest

from app.security import (
    CapabilityError,
    ConnectorCapability,
    CredentialIdentity,
    CredentialIntegrityError,
    KekMaterial,
    Keyring,
    KeyringError,
    connector_vault_capability,
    is_sensitive,
    load_keyring,
    open_oauth_credential,
    redact,
    rewrap_dek,
    seal_oauth_credential,
)


def _identity() -> CredentialIdentity:
    return CredentialIdentity(
        credential_id=uuid.uuid4(), connector_id=uuid.uuid4(), tenant_id=uuid.uuid4()
    )


def test_seal_open_round_trip() -> None:
    keyring = load_keyring()
    identity = _identity()
    token = {"refresh_token": "rt-123", "access_token": "at-456", "expires_in": 3600}

    env = seal_oauth_credential(token, identity, keyring)
    opened = open_oauth_credential(env, identity, connector_vault_capability(), keyring)
    assert opened == token


def test_open_requires_capability() -> None:
    keyring = load_keyring()
    identity = _identity()
    env = seal_oauth_credential({"refresh_token": "x"}, identity, keyring)
    with pytest.raises(CapabilityError):
        open_oauth_credential(env, identity, ConnectorCapability(object()), keyring)


def test_aad_mismatch_and_wrong_identity_fail() -> None:
    keyring = load_keyring()
    identity = _identity()
    env = seal_oauth_credential({"refresh_token": "x"}, identity, keyring)
    cap = connector_vault_capability()

    # tampered AAD
    tampered = dataclasses.replace(env, aad=env.aad + b" ")
    with pytest.raises(CredentialIntegrityError):
        open_oauth_credential(tampered, identity, cap, keyring)

    # different identity (different tenant) -> AAD mismatch
    other = dataclasses.replace(identity, tenant_id=uuid.uuid4())
    with pytest.raises(CredentialIntegrityError):
        open_oauth_credential(env, other, cap, keyring)


def test_ciphertext_tamper_fails() -> None:
    keyring = load_keyring()
    identity = _identity()
    env = seal_oauth_credential({"refresh_token": "x"}, identity, keyring)
    flipped = bytearray(env.ciphertext)
    flipped[0] ^= 0x01
    broken = dataclasses.replace(env, ciphertext=bytes(flipped))
    with pytest.raises(CredentialIntegrityError):
        open_oauth_credential(broken, identity, connector_vault_capability(), keyring)


def test_kek_rotation_rewrap_preserves_payload() -> None:
    keyring_a = load_keyring()
    active_a = keyring_a.active
    identity = _identity()
    token = {"refresh_token": "rt-rotate"}
    env_a = seal_oauth_credential(token, identity, keyring_a)

    # New active key; old key retained as previous.
    keyring_b = Keyring(
        KekMaterial(id="env-2", version=1, key=os.urandom(32)),
        previous={(active_a.id, active_a.version): active_a.key},
    )
    cap = connector_vault_capability()

    # Old envelope still opens while its KEK is retained.
    assert open_oauth_credential(env_a, identity, cap, keyring_b) == token

    # Rewrap to the new active KEK; payload unchanged; opens under new key.
    env_b = rewrap_dek(env_a, identity, keyring_b)
    assert env_b.kek_id == "env-2"
    assert env_b.ciphertext == env_a.ciphertext  # payload not re-encrypted
    assert open_oauth_credential(env_b, identity, cap, keyring_b) == token


def test_keyring_rejects_bad_material() -> None:
    with pytest.raises(KeyringError):
        Keyring(KekMaterial("a", 1, b"short"), {}).require("missing", 9)


def test_ciphertext_hides_canary_and_redaction_masks() -> None:
    keyring = load_keyring()
    identity = _identity()
    canary = f"SHERPA_CANARY_{uuid.uuid4()}"
    env = seal_oauth_credential({"refresh_token": canary}, identity, keyring)
    assert canary.encode() not in env.ciphertext
    assert canary.encode() not in env.encrypted_dek

    assert is_sensitive("refresh_token") and is_sensitive("Set-Cookie")
    masked = redact({"refresh_token": canary, "note": "ok", "nested": {"api_key": "k"}})
    assert masked["refresh_token"] == "***REDACTED***"
    assert masked["nested"]["api_key"] == "***REDACTED***"
    assert masked["note"] == "ok"
