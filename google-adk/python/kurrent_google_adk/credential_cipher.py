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
