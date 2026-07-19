"""Structured-log secret redaction (config §3.5).

Redacts values whose field/header names match (case-insensitive substring) the
sensitive-name set before anything reaches logs or persisted diagnostics. OAuth /
provider HTTP bodies are never logged at all; this is defense in depth for
structured fields.
"""

from __future__ import annotations

from typing import Any

REDACTED = "***REDACTED***"

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "token",
        "secret",
        "password",
        "credential",
        "kek",
        "api_key",
        "ciphertext",
        "encrypted_dek",
    }
)


def is_sensitive(name: str) -> bool:
    lname = name.lower()
    return any(key in lname for key in SENSITIVE_KEYS)


def redact(value: Any) -> Any:
    """Deep-copy `value`, masking values under sensitive keys."""
    if isinstance(value, dict):
        return {k: (REDACTED if is_sensitive(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    return value
