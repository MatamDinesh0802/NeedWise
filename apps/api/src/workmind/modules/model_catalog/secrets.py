"""Encryption of model provider API keys at rest: AES-256-GCM with key ids, so keys rotate.

    sealed = version (1 byte) | key id (4 bytes) | nonce (12 bytes) | ciphertext and tag

The active key comes from SECRETS_ENCRYPTION_KEY; older keys listed in
SECRETS_ENCRYPTION_PREVIOUS_KEYS still decrypt. A key id is the first 4 bytes of a SHA-256 of
the key, so a stored value names the key that opens it without revealing anything about it.
Each value gets a fresh 96-bit nonce, and the caller binds it to its context (tenant, provider,
kind and base URL) as associated data: a ciphertext copied to another provider or tenant, or
left behind when the base URL changes, does not decrypt.

Errors never contain key material or plaintext.
"""

import base64
import hashlib
import os
from collections.abc import Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from workmind.core.config import Settings

VERSION = 1
KID_BYTES = 4
NONCE_BYTES = 12
HEADER_BYTES = 1 + KID_BYTES + NONCE_BYTES


class SecretsError(Exception):
    """A value cannot be sealed or opened. The message is safe to log and show."""


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(b"workmind-secrets-key-id:" + key).digest()[:KID_BYTES]


def _decode(key_b64: str) -> bytes:
    try:
        key = base64.b64decode(key_b64, validate=True)
    except ValueError as exc:
        raise SecretsError("an encryption key is not valid base64") from exc
    if len(key) != 32:
        raise SecretsError("an encryption key does not decode to 32 bytes")
    return key


class KeyRing:
    def __init__(self, active_b64: str, previous_b64: Sequence[str] = ()) -> None:
        active = _decode(active_b64)
        self._active_id = key_id(active)
        self._keys: dict[bytes, AESGCM] = {self._active_id: AESGCM(active)}
        for value in previous_b64:
            key = _decode(value)
            self._keys.setdefault(key_id(key), AESGCM(key))

    @classmethod
    def from_settings(cls, settings: Settings) -> "KeyRing":
        active, previous = settings.encryption_keys
        return cls(active, previous)

    @property
    def active_key_id(self) -> str:
        return self._active_id.hex()

    def seal(self, plaintext: str, *, context: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._keys[self._active_id].encrypt(nonce, plaintext.encode(), context)
        return bytes([VERSION]) + self._active_id + nonce + sealed

    def open(self, sealed: bytes, *, context: bytes) -> str:
        if len(sealed) <= HEADER_BYTES or sealed[0] != VERSION:
            raise SecretsError("the stored value is not in a known format")
        kid, nonce = sealed[1 : 1 + KID_BYTES], sealed[1 + KID_BYTES : HEADER_BYTES]
        aead = self._keys.get(kid)
        if aead is None:
            raise SecretsError(
                f"the stored value was encrypted with key {kid.hex()}, which is not configured"
            )
        try:
            return aead.decrypt(nonce, sealed[HEADER_BYTES:], context).decode()
        except InvalidTag as exc:  # wrong context, or tampered with
            raise SecretsError("the stored value failed its integrity check") from exc

    def key_id_of(self, sealed: bytes) -> str:
        return sealed[1 : 1 + KID_BYTES].hex()

    def needs_rotation(self, sealed: bytes) -> bool:
        return sealed[1 : 1 + KID_BYTES] != self._active_id

    def rotate(self, sealed: bytes, *, context: bytes) -> bytes:
        """The same plaintext, sealed again with the active key."""
        return self.seal(self.open(sealed, context=context), context=context)
