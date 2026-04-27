"""Unit tests for credential ciphers.

These tests do not need KurrentDB — they exercise the cipher wire format
in isolation.
"""
from __future__ import annotations

import dataclasses
import inspect

import pytest

from kurrent_google_adk.credential_cipher import (
    CredentialCipher,
    CredentialContext,
    NullCredentialCipher,
)


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
