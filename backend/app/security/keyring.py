"""KEK keyring: active + previous key material from env (config §3.4, ADR-019).

v1 reads environment-backed KEKs (base64 of exactly 32 bytes). A later KMS
provider replaces raw key loading, not the envelope fields or rotation semantics.
"""

from __future__ import annotations

import base64
import dataclasses
import json

from app.config import settings


class KeyringError(Exception):
    """KEK material is missing, malformed, or the requested key is unknown."""


@dataclasses.dataclass(frozen=True)
class KekMaterial:
    id: str
    version: int
    key: bytes  # exactly 32 bytes (AES-256)


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001 - normalized to KeyringError
        raise KeyringError("KEK is not valid base64") from exc
    if len(key) != 32:
        raise KeyringError("KEK must decode to exactly 32 bytes")
    return key


class Keyring:
    """Resolves `(kek_id, key_version)` to raw key bytes; knows the active key."""

    def __init__(self, active: KekMaterial, previous: dict[tuple[str, int], bytes]) -> None:
        self._active = active
        self._previous = previous

    @property
    def active(self) -> KekMaterial:
        return self._active

    def require(self, kek_id: str, key_version: int) -> bytes:
        if kek_id == self._active.id and key_version == self._active.version:
            return self._active.key
        key = self._previous.get((kek_id, key_version))
        if key is None:
            raise KeyringError(f"unknown KEK {kek_id}:{key_version}")
        return key


def _parse_previous(raw: str) -> dict[tuple[str, int], bytes]:
    try:
        mapping = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise KeyringError("KEK_PREVIOUS_KEYS is not valid JSON") from exc
    out: dict[tuple[str, int], bytes] = {}
    for name, value in mapping.items():
        try:
            kek_id, version = name.rsplit(":", 1)
            out[(kek_id, int(version))] = _decode_key(value)
        except KeyringError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise KeyringError("previous key names must be '<kek_id>:<version>'") from exc
    return out


def load_keyring() -> Keyring:
    """Build the process keyring from settings. Raises KeyringError if unusable."""
    if not settings.kek_id:
        raise KeyringError("KEK_ID is required")
    active = KekMaterial(
        id=settings.kek_id,
        version=settings.kek_key_version,
        key=_decode_key(settings.kek),
    )
    return Keyring(active, _parse_previous(settings.kek_previous_keys))
