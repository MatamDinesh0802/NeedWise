"""Encryption of connector credentials at rest (AES-256-GCM).

The key comes from SECRETS_KEY (32 bytes, base64). Each value gets a fresh 96-bit nonce, and the
source id is bound in as associated data, so a ciphertext copied onto another source does not
decrypt. Rotating the key means re-entering credentials (or a re-encryption script).
"""

import base64
import json
import os
import uuid
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


class SecretsError(Exception):
    pass


class SecretBox:
    def __init__(self, key_b64: str) -> None:
        try:
            key = base64.b64decode(key_b64, validate=True)
        except ValueError as exc:
            raise SecretsError("SECRETS_KEY must be base64") from exc
        if len(key) != 32:
            raise SecretsError("SECRETS_KEY must decode to 32 bytes")
        self._aead = AESGCM(key)

    def seal(self, source_id: uuid.UUID, value: dict[str, Any]) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        plain = json.dumps(value, separators=(",", ":")).encode()
        return nonce + self._aead.encrypt(nonce, plain, source_id.bytes)

    def open(self, source_id: uuid.UUID, sealed: bytes) -> dict[str, Any]:
        try:
            plain = self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], source_id.bytes)
        except Exception as exc:  # InvalidTag: wrong key, wrong source, or tampered
            raise SecretsError("credentials cannot be decrypted") from exc
        value = json.loads(plain)
        if not isinstance(value, dict):
            raise SecretsError("credentials are malformed")
        return value  # pyright: ignore[reportUnknownVariableType]
