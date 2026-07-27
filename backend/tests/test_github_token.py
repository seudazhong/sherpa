"""GitHub connection token AEAD seal/open (ADR-019/038). No DB required."""

from __future__ import annotations

import uuid

import pytest

from app.security import (
    GithubTokenIdentity,
    classify_github_token,
    connector_vault_capability,
    load_keyring,
    open_github_token,
    seal_github_token,
)
from app.security.vault import CapabilityError, CredentialIntegrityError


def _identity() -> GithubTokenIdentity:
    return GithubTokenIdentity(
        tenant_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        auth_kind="pat",
    )


def test_seal_open_round_trip() -> None:
    identity = _identity()
    keyring = load_keyring()
    token = "github_pat_secret_value_1234567890"  # noqa: S105 - test literal
    seal = seal_github_token(token, identity, keyring)
    # The ciphertext must not contain the plaintext token.
    assert token.encode() not in seal.token_enc
    got = open_github_token(seal, identity, connector_vault_capability(), keyring)
    assert got == token


def test_open_requires_capability() -> None:
    identity = _identity()
    keyring = load_keyring()
    seal = seal_github_token("t0ken", identity, keyring)  # noqa: S106
    with pytest.raises(CapabilityError):
        open_github_token(seal, identity, object(), keyring)  # type: ignore[arg-type]


def test_open_rejects_wrong_identity() -> None:
    keyring = load_keyring()
    seal = seal_github_token("t0ken", _identity(), keyring)  # noqa: S106
    with pytest.raises(CredentialIntegrityError):
        open_github_token(seal, _identity(), connector_vault_capability(), keyring)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("github_pat_11ABCDEFG0123456789", "fine_grained_pat"),
        ("ghp_classic0123456789", "classic_pat"),
        ("gho_oauth0123456789", "oauth"),
        ("ghu_user0123456789", "app_user_to_server"),
        ("ghs_install0123456789", "app_installation"),
        ("ghr_refresh0123456789", "refresh"),
        ("  github_pat_padded  ", "fine_grained_pat"),
        ("", "other"),
        ("   ", "other"),
        ("not-a-github-token", "other"),
        ("gh_", "other"),
    ],
)
def test_classify_github_token_categories(token: str, expected: str) -> None:
    assert classify_github_token(token) == expected  # noqa: S105 - test literals


def test_classify_never_leaks_token_material() -> None:
    # A category label must carry no fragment/length/hash of the credential.
    secret = "github_pat_HIGHLYSENSITIVEVALUE9876543210"  # noqa: S105 - test literal
    label = classify_github_token(secret)
    assert label == "fine_grained_pat"
    assert secret not in label
    assert "9876543210" not in label
    assert str(len(secret)) not in label
