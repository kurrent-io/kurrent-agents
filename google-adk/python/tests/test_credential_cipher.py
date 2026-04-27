"""Unit tests for credential ciphers.

These tests do not need KurrentDB — they exercise the cipher wire format
in isolation.
"""
from __future__ import annotations

import dataclasses
import inspect
import os

import pytest
from cryptography.exceptions import InvalidTag

from kurrent_google_adk.credential_cipher import (
    AesGcmCredentialCipher,
    CredentialCipher,
    CredentialContext,
    NullCredentialCipher,
)


def _key() -> bytes:
    return os.urandom(32)


def test_credential_context_is_frozen() -> None:
    ctx = CredentialContext(app_name="app", user_id="user", credential_key="k")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.app_name = "other"  # type: ignore[misc]


def test_protocol_has_encrypt_and_decrypt() -> None:
    """The protocol exposes encrypt/decrypt with `(plaintext|ciphertext, context)` shape."""
    encrypt_sig = inspect.signature(CredentialCipher.encrypt, eval_str=True)
    decrypt_sig = inspect.signature(CredentialCipher.decrypt, eval_str=True)

    assert list(encrypt_sig.parameters) == ["self", "plaintext", "context"]
    assert encrypt_sig.return_annotation is bytes
    assert list(decrypt_sig.parameters) == ["self", "ciphertext", "context"]
    assert decrypt_sig.return_annotation is bytes


def test_concrete_class_satisfies_protocol() -> None:
    """A class implementing the two methods structurally satisfies CredentialCipher."""
    class _Impl:
        def encrypt(self, plaintext: bytes, context: CredentialContext) -> bytes:
            return plaintext

        def decrypt(self, ciphertext: bytes, context: CredentialContext) -> bytes:
            return ciphertext

    impl: CredentialCipher = _Impl()  # type: ignore[assignment]
    ctx = CredentialContext("a", "u", "k")
    assert impl.encrypt(b"x", ctx) == b"x"
    assert impl.decrypt(b"x", ctx) == b"x"


class TestNullCredentialCipher:
    def test_round_trip(self) -> None:
        cipher = NullCredentialCipher()
        ctx = CredentialContext("app", "user", "k")
        plaintext = b'{"hello": "world"}'

        wire = cipher.encrypt(plaintext, ctx)
        assert wire[0] == 0x00, "version byte must be 0x00"
        assert plaintext in wire, "null cipher does not encrypt"

        decoded = cipher.decrypt(wire, ctx)
        assert decoded == plaintext

    def test_decrypt_rejects_wrong_version(self) -> None:
        cipher = NullCredentialCipher()
        ctx = CredentialContext("app", "user", "k")
        with pytest.raises(ValueError, match="version"):
            cipher.decrypt(b"\x01garbage", ctx)

    def test_decrypt_rejects_empty(self) -> None:
        cipher = NullCredentialCipher()
        ctx = CredentialContext("app", "user", "k")
        with pytest.raises(ValueError, match="empty"):
            cipher.decrypt(b"", ctx)


class TestAesGcmCredentialCipherRoundTrip:
    def test_round_trip(self) -> None:
        cipher = AesGcmCredentialCipher(keys=[_key()])
        ctx = CredentialContext("app", "user", "k")
        plaintext = b'{"refresh_token": "secret"}'

        wire = cipher.encrypt(plaintext, ctx)
        assert wire[0] == 0x01
        assert wire[1] == 0x00, "first key has key_id=0"
        assert plaintext not in wire, "plaintext must not appear in ciphertext"

        decoded = cipher.decrypt(wire, ctx)
        assert decoded == plaintext

    def test_random_nonce(self) -> None:
        cipher = AesGcmCredentialCipher(keys=[_key()])
        ctx = CredentialContext("app", "user", "k")
        wires = {cipher.encrypt(b"same", ctx) for _ in range(8)}
        assert len(wires) == 8, "each encryption must use a fresh nonce"


class TestAesGcmCredentialCipherAad:
    def test_aad_binding_rejects_relocation(self) -> None:
        cipher = AesGcmCredentialCipher(keys=[_key()])
        wire = cipher.encrypt(b"secret", CredentialContext("app", "alice", "k"))
        with pytest.raises(InvalidTag):  # InvalidTag from cryptography
            cipher.decrypt(wire, CredentialContext("app", "bob", "k"))

    def test_aad_binding_rejects_different_credential_key(self) -> None:
        cipher = AesGcmCredentialCipher(keys=[_key()])
        wire = cipher.encrypt(b"secret", CredentialContext("app", "user", "k1"))
        with pytest.raises(InvalidTag):
            cipher.decrypt(wire, CredentialContext("app", "user", "k2"))


class TestAesGcmCredentialCipherRotation:
    def test_decrypts_with_old_key_after_rotation(self) -> None:
        old, new = _key(), _key()
        old_cipher = AesGcmCredentialCipher(keys=[old])
        ctx = CredentialContext("app", "user", "k")
        old_wire = old_cipher.encrypt(b"old", ctx)

        rotated = AesGcmCredentialCipher(keys=[new, old])
        new_wire = rotated.encrypt(b"new", ctx)

        assert rotated.decrypt(old_wire, ctx) == b"old"
        assert rotated.decrypt(new_wire, ctx) == b"new"
        assert new_wire[1] == 0x00, "new key sits at index 0"
        assert old_wire[1] == 0x00, "old wire was written when old was at index 0"

    def test_unknown_key_id_raises(self) -> None:
        from kurrent_google_adk.credential_cipher import UnknownKeyIdError

        # Encrypt with key A at index 0, then drop A so its key_id (0) becomes
        # the new key's slot — but because the new key bytes differ, decrypt
        # under the new key with key_id 0 will fail with InvalidTag *unless*
        # we tag the ciphertext with a key_id that has no corresponding key.
        cipher = AesGcmCredentialCipher(keys=[_key()])
        wire = cipher.encrypt(b"hi", CredentialContext("app", "user", "k"))
        # Forge a key_id of 0xFF (no such key) and replay the rest.
        forged = bytes([wire[0], 0xFF]) + wire[2:]
        with pytest.raises(UnknownKeyIdError):
            cipher.decrypt(forged, CredentialContext("app", "user", "k"))


class TestAesGcmCredentialCipherErrors:
    def test_truncated_wire_raises(self) -> None:
        from kurrent_google_adk.credential_cipher import MalformedCiphertextError

        cipher = AesGcmCredentialCipher(keys=[_key()])
        ctx = CredentialContext("app", "user", "k")
        wire = cipher.encrypt(b"x", ctx)
        # 1 + 1 + 12 + 16 = 30 bytes minimum (zero-byte ciphertext + tag).
        with pytest.raises(MalformedCiphertextError):
            cipher.decrypt(wire[:20], ctx)

    def test_unsupported_version_raises(self) -> None:
        from kurrent_google_adk.credential_cipher import UnsupportedCipherVersionError

        cipher = AesGcmCredentialCipher(keys=[_key()])
        ctx = CredentialContext("app", "user", "k")
        with pytest.raises(UnsupportedCipherVersionError):
            cipher.decrypt(b"\x99" + b"\x00" * 30, ctx)

    def test_requires_at_least_one_key(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            AesGcmCredentialCipher(keys=[])

    def test_too_many_keys_rejected(self) -> None:
        with pytest.raises(ValueError, match="256"):
            AesGcmCredentialCipher(keys=[_key() for _ in range(257)])

    def test_rejects_wrong_key_length(self) -> None:
        with pytest.raises(ValueError):
            AesGcmCredentialCipher(keys=[b"\x00" * 16])  # AES-128, not 256
