"""Security primitives: KEK keyring, credential vault (AEAD), log redaction."""

from __future__ import annotations

from app.security.connector_token import (
    ConnectorSeal,
    ConnectorTokenIdentity,
    open_connector_token,
    seal_connector_token,
)
from app.security.github_token import (
    FINE_GRAINED_PAT_PREFIX,
    GithubSeal,
    GithubTokenIdentity,
    classify_github_token,
    open_github_token,
    seal_github_token,
)
from app.security.keyring import KekMaterial, Keyring, KeyringError, load_keyring
from app.security.redaction import REDACTED, SENSITIVE_KEYS, is_sensitive, redact
from app.security.vault import (
    CapabilityError,
    ConnectorCapability,
    CredentialEnvelope,
    CredentialIdentity,
    CredentialIntegrityError,
    VaultError,
    connector_vault_capability,
    open_oauth_credential,
    rewrap_dek,
    seal_oauth_credential,
)

__all__ = [
    "KekMaterial",
    "Keyring",
    "KeyringError",
    "load_keyring",
    "REDACTED",
    "SENSITIVE_KEYS",
    "is_sensitive",
    "redact",
    "CapabilityError",
    "ConnectorCapability",
    "CredentialEnvelope",
    "CredentialIdentity",
    "CredentialIntegrityError",
    "VaultError",
    "connector_vault_capability",
    "open_oauth_credential",
    "rewrap_dek",
    "seal_oauth_credential",
    "ConnectorSeal",
    "ConnectorTokenIdentity",
    "open_connector_token",
    "seal_connector_token",
    "GithubSeal",
    "GithubTokenIdentity",
    "open_github_token",
    "seal_github_token",
    "classify_github_token",
    "FINE_GRAINED_PAT_PREFIX",
]
