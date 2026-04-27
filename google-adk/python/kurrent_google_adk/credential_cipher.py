"""Pluggable credential ciphers for ``KurrentDBCredentialService``.

See ``docs/superpowers/specs/2026-04-27-adk-credential-service-design.md``
for the wire format and threat model. Two implementations ship with the
package:

* :class:`NullCredentialCipher` — version ``0x00``, plaintext (tests/opt-out).
* :class:`AesGcmCredentialCipher` — version ``0x01``, AES-256-GCM with AAD
  binding to ``(app_name, user_id, credential_key)`` (recommended default;
  requires the ``[crypto]`` extra).

Adding a new cipher version is additive: pick the next free version byte,
implement the wire format, and the existing event shape continues to work.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CredentialContext:
    """Authenticated context bound into the ciphertext via AEAD AAD.

    Binding to all three fields stops a database-level attacker from
    relocating a ciphertext blob between users or between credential keys
    on the same user — decryption will fail because the AAD bytes don't
    match.
    """

    app_name: str
    user_id: str
    credential_key: str

    def aad(self) -> bytes:
        return f"{self.app_name}|{self.user_id}|{self.credential_key}".encode()


class CredentialCipher(Protocol):
    """Encrypt/decrypt a credential payload with an authenticated context.

    Implementations own their wire format; the first byte must be a
    version that distinguishes them from every other registered cipher.
    """

    def encrypt(self, plaintext: bytes, context: CredentialContext) -> bytes: ...

    def decrypt(self, ciphertext: bytes, context: CredentialContext) -> bytes: ...


NULL_CIPHER_VERSION: int = 0x00


class NullCredentialCipher:
    """Plaintext "cipher" with the same outer container as real ciphers.

    Used by tests and by deployments that explicitly opt out of
    encryption. The wire format is ``b"\\x00" + plaintext`` so readers
    branch on a single version byte regardless of which cipher wrote
    the event.
    """

    def encrypt(self, plaintext: bytes, context: CredentialContext) -> bytes:
        del context  # unused for null
        return bytes([NULL_CIPHER_VERSION]) + plaintext

    def decrypt(self, ciphertext: bytes, context: CredentialContext) -> bytes:
        del context  # unused for null
        if not ciphertext:
            raise ValueError("Cannot decrypt empty ciphertext")
        if ciphertext[0] != NULL_CIPHER_VERSION:
            raise ValueError(
                f"NullCredentialCipher cannot decrypt version 0x{ciphertext[0]:02x}"
            )
        return ciphertext[1:]


AES_GCM_VERSION: int = 0x01
_NONCE_LEN: int = 12
_TAG_LEN: int = 16
_KEY_LEN: int = 32  # AES-256
_HEADER_LEN: int = 2  # version + key_id
_MIN_WIRE_LEN: int = _HEADER_LEN + _NONCE_LEN + _TAG_LEN


class MalformedCiphertextError(ValueError):
    """Wire blob is shorter than the minimum for its declared version."""


class UnsupportedCipherVersionError(ValueError):
    """Wire blob's version byte is not handled by this cipher."""


class UnknownKeyIdError(ValueError):
    """Wire blob's key_id has no corresponding key in the cipher's key list."""


class AesGcmCredentialCipher:
    """AES-256-GCM credential cipher with AAD context binding.

    Encrypt always uses ``keys[0]`` and tags the wire with ``key_id=0``.
    Decrypt picks the keyed-by-id position as a fast path, then falls
    back to trial-decryption against the other keys on ``InvalidTag``.
    The fallback is what makes prepend-style rotation work: an old
    ciphertext written when key K was at index 0 still decrypts after
    a new key has been prepended (K is now at index 1).

    Rotation procedure:

    1. Prepend the new key: ``keys=[new, old]``. Existing ciphertexts
       still decrypt (via trial fallback to ``old``); new writes are
       encrypted under ``new``.
    2. Optional: re-encrypt every existing ``CredentialSaved`` event by
       reading + writing through the service. Append-only + load-latest
       semantics naturally retire the old-key ciphertexts.
    3. Once no ciphertext is still encrypted under the old key, drop
       it: ``keys=[new]``. Anything still tagged for the dropped key
       raises ``InvalidTag`` (or ``UnknownKeyIdError`` if its ``key_id``
       is now out of range).

    Raises :class:`ImportError` at construction if ``cryptography`` is
    not installed (install via the ``[crypto]`` extra).
    """

    def __init__(self, keys: Sequence[bytes]) -> None:
        if not keys:
            raise ValueError("AesGcmCredentialCipher requires at least one key")
        if len(keys) > 256:
            raise ValueError(
                "AesGcmCredentialCipher supports at most 256 keys (key_id is 1 byte)"
            )
        try:
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "AesGcmCredentialCipher requires the cryptography package. "
                "Install with: pip install kurrent-google-adk[crypto]"
            ) from exc

        for i, key in enumerate(keys):
            if len(key) != _KEY_LEN:
                raise ValueError(
                    f"Key at index {i} is {len(key)} bytes; AES-256-GCM requires {_KEY_LEN}"
                )

        self._aesgcms = [AESGCM(bytes(k)) for k in keys]
        self._invalid_tag: type[Exception] = InvalidTag

    def encrypt(self, plaintext: bytes, context: CredentialContext) -> bytes:
        nonce = os.urandom(_NONCE_LEN)
        body = self._aesgcms[0].encrypt(nonce, plaintext, context.aad())
        # body = ciphertext || tag (the cryptography library appends the tag).
        return bytes([AES_GCM_VERSION, 0x00]) + nonce + body

    def decrypt(self, ciphertext: bytes, context: CredentialContext) -> bytes:
        if len(ciphertext) < _MIN_WIRE_LEN:
            raise MalformedCiphertextError(
                f"Wire blob is {len(ciphertext)} bytes; minimum is {_MIN_WIRE_LEN}"
            )
        version = ciphertext[0]
        if version != AES_GCM_VERSION:
            raise UnsupportedCipherVersionError(
                f"AesGcmCredentialCipher cannot decrypt version 0x{version:02x}"
            )
        key_id = ciphertext[1]
        if key_id >= len(self._aesgcms):
            raise UnknownKeyIdError(
                f"key_id 0x{key_id:02x} not in this cipher's key list (len={len(self._aesgcms)})"
            )
        nonce = ciphertext[_HEADER_LEN : _HEADER_LEN + _NONCE_LEN]
        body = ciphertext[_HEADER_LEN + _NONCE_LEN :]
        aad = context.aad()
        # Try the key at the tagged index first; on InvalidTag, fall back to
        # trial decryption against the other keys in the list. This supports
        # rotation: old wires tagged with key_id=0 written under the previous
        # generation key still decrypt after a new key is prepended.
        try:
            return self._aesgcms[key_id].decrypt(nonce, body, aad)
        except self._invalid_tag:
            for i, aesgcm in enumerate(self._aesgcms):
                if i == key_id:
                    continue
                try:
                    return aesgcm.decrypt(nonce, body, aad)
                except self._invalid_tag:
                    continue
            raise
