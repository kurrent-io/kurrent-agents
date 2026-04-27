"""Unit tests for credential ciphers.

These tests do not need KurrentDB — they exercise the cipher wire format
in isolation.
"""
from __future__ import annotations

import pytest

from kurrent_google_adk.credential_cipher import (
    CredentialContext,
    CredentialCipher,
)


def test_credential_context_is_frozen() -> None:
    ctx = CredentialContext(app_name="app", user_id="user", credential_key="k")
    with pytest.raises(Exception):  # FrozenInstanceError under dataclass(frozen=True)
        ctx.app_name = "other"  # type: ignore[misc]


def test_protocol_has_encrypt_and_decrypt() -> None:
    # Smoke: protocol exists and exposes the two methods we rely on.
    assert hasattr(CredentialCipher, "encrypt")
    assert hasattr(CredentialCipher, "decrypt")
