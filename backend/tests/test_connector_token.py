"""Connector-token sealing (m2-14): direct AES-256-GCM round-trip + AAD/tamper.
Pure crypto — no DB/Redis."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from app.security import (
    ConnectorTokenIdentity,
    CredentialIntegrityError,
    connector_vault_capability,
    load_keyring,
    open_connector_token,
    seal_connector_token,
)


def _identity() -> ConnectorTokenIdentity:
    return ConnectorTokenIdentity(
        tenant_id=uuid.uuid4(), connector_id=uuid.uuid4(), external_account_id="a@gmail.com"
    )


def test_connector_token_round_trip() -> None:
    keyring = load_keyring()
    identity = _identity()
    token = {"refresh_token": "rt-1", "access_token": "at-1", "scope": "gmail.readonly"}

    seal = seal_connector_token(token, identity, keyring)
    assert seal.token_algorithm == "AES-256-GCM"
    assert len(seal.nonce) == 12 and len(seal.token_enc) >= 16

    opened = open_connector_token(seal, identity, connector_vault_capability(), keyring)
    assert opened == token


def test_connector_token_wrong_identity_and_tamper_fail() -> None:
    keyring = load_keyring()
    identity = _identity()
    seal = seal_connector_token({"refresh_token": "rt"}, identity, keyring)
    cap = connector_vault_capability()

    other = dataclasses.replace(identity, external_account_id="b@gmail.com")
    with pytest.raises(CredentialIntegrityError):
        open_connector_token(seal, other, cap, keyring)

    flipped = bytearray(seal.token_enc)
    flipped[0] ^= 0x01
    with pytest.raises(CredentialIntegrityError):
        open_connector_token(
            dataclasses.replace(seal, token_enc=bytes(flipped)), identity, cap, keyring
        )
