# ADK Credential Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `KurrentDBCredentialService` stub with a working implementation that persists ADK OAuth credentials to `Credentials-{app_name}-{user_id}` streams as encrypted `CredentialSaved` events.

**Architecture:** ADK-specific Pydantic event registered alongside the existing four (`AgentTransferred`, `Rewind`, `Compaction`, `StateDelta`); a small `CredentialCipher` protocol with two ships-with-the-package implementations (`NullCredentialCipher` for tests, `AesGcmCredentialCipher` as the recommended default); service uses backward stream scan + load-latest-by-key. Spec: `docs/superpowers/specs/2026-04-27-adk-credential-service-design.md`.

**Tech Stack:** Python 3.11, Pydantic 2.5, `kurrentdbclient` 1.2 (async), `kurrent-agent-schema` 0.1.1, `cryptography` ≥ 42 (optional `[crypto]` extra), `google-adk` ≥ 1.0, pytest 8 with testcontainers via `kurrent-agents-testing`.

**Spec acceptance reminders:**
- ADK exposes `auth_config.credential_key` as a **property** (the `get_credential_key()` method is `@deprecated`). Use the property.
- `callback_context._invocation_context.app_name` / `.user_id` is the supported access path — `InMemoryCredentialService` does the same.
- The Credentials stream is ADK-only; the event type stays out of the canonical schema.

---

## Task 1: Add `CredentialSaved` event type

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/events.py`
- Test: `google-adk/python/tests/test_serialization.py` (existing — extend with one round-trip case)

- [ ] **Step 1: Write the failing serialization round-trip test**

Append to `google-adk/python/tests/test_serialization.py` (find the last test in the file and add this after it; if a `TestAdkLocalEvents` class exists, add the method there):

```python
def test_credential_saved_round_trip() -> None:
    from datetime import datetime, timezone

    from kurrent_google_adk._serialization import deserialize, name_for, serialize
    from kurrent_google_adk.events import CredentialSaved

    event = CredentialSaved(
        credential_key="oauth2:scope=read",
        credential="AQABAGRlYWRiZWVm",  # dummy base64
        timestamp=datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc),
    )
    assert name_for(event) == "CredentialSaved"

    new_event = serialize(event)
    assert new_event.type == "CredentialSaved"

    class _Recorded:
        type = new_event.type
        data = new_event.data
        metadata = new_event.metadata
        stream_name = "Credentials-app-user"
        stream_position = 0

    decoded = deserialize(_Recorded())  # type: ignore[arg-type]
    assert isinstance(decoded, CredentialSaved)
    assert decoded.credential_key == event.credential_key
    assert decoded.credential == event.credential
    assert decoded.timestamp == event.timestamp
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_serialization.py::test_credential_saved_round_trip -v`
Expected: FAIL — `ImportError: cannot import name 'CredentialSaved' from 'kurrent_google_adk.events'`.

- [ ] **Step 3: Add `CredentialSaved` to `events.py`**

Edit `google-adk/python/kurrent_google_adk/events.py`. Add the class after `StateDelta` (preserving its placement in the existing block of ADK-specific types):

```python
class CredentialSaved(_EventBase):
    """ADK-specific event recording a tool OAuth credential.

    The ``credential`` field is a base64-encoded, cipher-self-describing
    wire blob. Format and AAD binding are defined in
    ``docs/superpowers/specs/2026-04-27-adk-credential-service-design.md``.
    """

    credential_key: str
    credential: str
    timestamp: datetime
```

Then add `"CredentialSaved"` to the `__all__` list (in the ADK-specific group, after `"StateDelta"`).

- [ ] **Step 4: Test still fails — registration is missing**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_serialization.py::test_credential_saved_round_trip -v`
Expected: FAIL — `ValueError: Unknown event type: CredentialSaved` from `name_for()`.

- [ ] **Step 5: Register `CredentialSaved` in `_serialization.py`**

Edit `google-adk/python/kurrent_google_adk/_serialization.py`:

```python
from .events import AgentTransferred, Compaction, CredentialSaved, Rewind, StateDelta

_ADK_LOCAL_TYPES: dict[type[_EventBase], str] = {
    AgentTransferred: "AgentTransferred",
    Rewind: "Rewind",
    Compaction: "Compaction",
    StateDelta: "StateDelta",
    CredentialSaved: "CredentialSaved",
}
```

- [ ] **Step 6: Test now passes**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_serialization.py::test_credential_saved_round_trip -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add google-adk/python/kurrent_google_adk/events.py google-adk/python/kurrent_google_adk/_serialization.py google-adk/python/tests/test_serialization.py
git commit -m "feat(adk): add CredentialSaved event type (DEV-1481)"
```

---

## Task 2: `CredentialContext` and `CredentialCipher` protocol

**Files:**
- Create: `google-adk/python/kurrent_google_adk/credential_cipher.py`
- Create: `google-adk/python/tests/test_credential_cipher.py`

- [ ] **Step 1: Write the failing protocol-shape test**

Create `google-adk/python/tests/test_credential_cipher.py`:

```python
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
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_cipher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kurrent_google_adk.credential_cipher'`.

- [ ] **Step 3: Create `credential_cipher.py` with the protocol and context**

Create `google-adk/python/kurrent_google_adk/credential_cipher.py`:

```python
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
        return f"{self.app_name}|{self.user_id}|{self.credential_key}".encode("utf-8")


class CredentialCipher(Protocol):
    """Encrypt/decrypt a credential payload with an authenticated context.

    Implementations own their wire format; the first byte must be a
    version that distinguishes them from every other registered cipher.
    """

    def encrypt(self, plaintext: bytes, context: CredentialContext) -> bytes: ...

    def decrypt(self, ciphertext: bytes, context: CredentialContext) -> bytes: ...
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_cipher.py -v`
Expected: PASS (2/2).

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/kurrent_google_adk/credential_cipher.py google-adk/python/tests/test_credential_cipher.py
git commit -m "feat(adk): add CredentialCipher protocol + CredentialContext (DEV-1481)"
```

---

## Task 3: `NullCredentialCipher` (version `0x00`)

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/credential_cipher.py`
- Modify: `google-adk/python/tests/test_credential_cipher.py`

- [ ] **Step 1: Write failing tests for the null cipher**

Append to `google-adk/python/tests/test_credential_cipher.py`:

```python
from kurrent_google_adk.credential_cipher import NullCredentialCipher


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
        import pytest

        cipher = NullCredentialCipher()
        ctx = CredentialContext("app", "user", "k")
        with pytest.raises(ValueError, match="version"):
            cipher.decrypt(b"\x01garbage", ctx)

    def test_decrypt_rejects_empty(self) -> None:
        import pytest

        cipher = NullCredentialCipher()
        ctx = CredentialContext("app", "user", "k")
        with pytest.raises(ValueError, match="empty"):
            cipher.decrypt(b"", ctx)
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_cipher.py -v`
Expected: FAIL — `ImportError: cannot import name 'NullCredentialCipher'`.

- [ ] **Step 3: Implement `NullCredentialCipher`**

Append to `google-adk/python/kurrent_google_adk/credential_cipher.py`:

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_cipher.py -v`
Expected: PASS (5/5 — 2 from Task 2 plus 3 new).

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/kurrent_google_adk/credential_cipher.py google-adk/python/tests/test_credential_cipher.py
git commit -m "feat(adk): add NullCredentialCipher (DEV-1481)"
```

---

## Task 4: Add `[crypto]` extras dependency

**Files:**
- Modify: `google-adk/python/pyproject.toml`

- [ ] **Step 1: Add the optional dependency**

Edit `google-adk/python/pyproject.toml`. In the `[project.optional-dependencies]` table (which already contains `dev = [...]`), add a sibling group:

```toml
[project.optional-dependencies]
dev = [
  "pytest >= 8",
  "pytest-asyncio >= 0.23",
  "ruff >= 0.6",
  "kurrent-agents-testing",
  "cryptography >= 42",
]
crypto = [
  "cryptography >= 42",
]
```

`cryptography` is added to **both** groups: `crypto` is the public extra; `dev` includes it so the cipher unit tests and integration tests can import it without a separate install dance.

- [ ] **Step 2: Sync the lock file**

Run: `uv sync --project google-adk/python --extra dev`
Expected: succeeds; `cryptography` shows in the install log.

- [ ] **Step 3: Smoke check the import**

Run: `uv run --project google-adk/python python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; AESGCM(b'\\x00' * 32); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add google-adk/python/pyproject.toml google-adk/python/uv.lock
git commit -m "build(adk): add [crypto] extra + cryptography dev dep (DEV-1481)"
```

> If `uv.lock` is not in the repo (check `git ls-files google-adk/python/uv.lock`), drop it from `git add`.

---

## Task 5: `AesGcmCredentialCipher` (version `0x01`)

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/credential_cipher.py`
- Modify: `google-adk/python/tests/test_credential_cipher.py`

Wire format: `| 1B version=0x01 | 1B key_id | 12B nonce | N bytes ciphertext | 16B tag |` with AAD = `f"{app_name}|{user_id}|{credential_key}".encode("utf-8")`.

- [ ] **Step 1: Write failing round-trip and AAD-binding tests**

Append to `google-adk/python/tests/test_credential_cipher.py`:

```python
import os
import pytest

from kurrent_google_adk.credential_cipher import AesGcmCredentialCipher


def _key() -> bytes:
    return os.urandom(32)


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
        with pytest.raises(Exception):  # InvalidTag from cryptography
            cipher.decrypt(wire, CredentialContext("app", "bob", "k"))

    def test_aad_binding_rejects_different_credential_key(self) -> None:
        cipher = AesGcmCredentialCipher(keys=[_key()])
        wire = cipher.encrypt(b"secret", CredentialContext("app", "user", "k1"))
        with pytest.raises(Exception):
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
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_cipher.py -v`
Expected: FAIL — `ImportError: cannot import name 'AesGcmCredentialCipher'`.

- [ ] **Step 3: Implement the AES-GCM cipher**

Append to `google-adk/python/kurrent_google_adk/credential_cipher.py`:

```python
import os
from collections.abc import Sequence

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

    Encrypt with ``keys[0]``; decrypt by ``key_id`` lookup. Rotation:
    prepend the new key to ``keys`` and keep the old key at the end of
    the list until no old ciphertexts remain.

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
        return self._aesgcms[key_id].decrypt(nonce, body, context.aad())
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_cipher.py -v`
Expected: PASS — all cipher tests green (the suite size grows by ~9 from this task).

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/kurrent_google_adk/credential_cipher.py google-adk/python/tests/test_credential_cipher.py
git commit -m "feat(adk): add AesGcmCredentialCipher with AAD binding (DEV-1481)"
```

---

## Task 6: Implement `KurrentDBCredentialService.save_credential`

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/credential_service.py`
- Test: `google-adk/python/tests/test_credential_service.py` (new file)

- [ ] **Step 1: Add a fake `CallbackContext` helper**

Create `google-adk/python/tests/test_credential_service.py`:

```python
"""Integration tests for ``KurrentDBCredentialService`` against a live KurrentDB."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from google.adk.auth.auth_credential import AuthCredential, AuthCredentialTypes, HttpAuth, HttpCredentials
from google.adk.auth.auth_schemes import AuthSchemeType
from google.adk.auth.auth_tool import AuthConfig
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_google_adk import KurrentDBCredentialService
from kurrent_google_adk.credential_cipher import (
    AesGcmCredentialCipher,
    NullCredentialCipher,
)


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}")


@dataclass
class _FakeInvocationContext:
    app_name: str
    user_id: str


@dataclass
class _FakeCallbackContext:
    """Minimal stand-in matching ``InMemoryCredentialService``'s access path."""

    _invocation_context: _FakeInvocationContext


def _ctx(app: str, user: str) -> _FakeCallbackContext:
    return _FakeCallbackContext(_invocation_context=_FakeInvocationContext(app, user))


def _auth_config(scope: str = "drive.readonly") -> AuthConfig:
    """Build an `AuthConfig` with a fully populated `exchanged_auth_credential`.

    Uses the simplest credential type the schema supports (HTTP basic) — the
    service must round-trip any `AuthCredential`, but tests don't depend on
    OAuth-specific shape.
    """
    cred = AuthCredential(
        auth_type=AuthCredentialTypes.HTTP,
        http=HttpAuth(
            scheme="basic",
            credentials=HttpCredentials(username="alice", password=f"pw-{scope}"),
        ),
    )
    config = AuthConfig.model_construct(
        auth_scheme={"type_": AuthSchemeType.http, "scheme": "basic"},  # type: ignore[arg-type]
        raw_auth_credential=None,
        exchanged_auth_credential=cred,
        credential_key=f"http:basic:{scope}",
    )
    return config
```

- [ ] **Step 2: Write the failing save-then-load round-trip test (Null cipher)**

Append to `google-adk/python/tests/test_credential_service.py`:

```python
class TestSaveAndLoadNull:
    async def test_round_trip(self, kurrentdb_client: AsyncKurrentDBClient) -> None:
        service = KurrentDBCredentialService(kurrentdb_client, cipher=NullCredentialCipher())
        app, user = _ids()
        cfg = _auth_config()

        await service.save_credential(cfg, _ctx(app, user))  # type: ignore[arg-type]
        loaded = await service.load_credential(cfg, _ctx(app, user))  # type: ignore[arg-type]

        assert loaded is not None
        assert loaded.auth_type == cfg.exchanged_auth_credential.auth_type
        assert loaded.http.credentials.username == "alice"
        assert loaded.http.credentials.password == "pw-drive.readonly"
```

- [ ] **Step 3: Run the test and watch it fail**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_service.py -v -k test_round_trip`
Expected: FAIL — `NotImplementedError` from the existing stub.

- [ ] **Step 4: Implement `save_credential` (and the now-required constructor)**

Replace `google-adk/python/kurrent_google_adk/credential_service.py` with:

```python
"""``KurrentDBCredentialService`` — see ``DESIGN.md`` §7.4.

Implements ``google.adk.auth.credential_service.BaseCredentialService`` on a
KurrentDB ``Credentials-{app}-{user}`` stream. ADK-specific; no AFW or
Strands analogue, so the event type ``CredentialSaved`` lives outside the
canonical vocabulary and is never emitted by other integrations.

A pluggable :class:`~.credential_cipher.CredentialCipher` is required at
construction. The recommended default is
:class:`~.credential_cipher.AesGcmCredentialCipher` (AES-256-GCM with AAD
binding). :class:`~.credential_cipher.NullCredentialCipher` is the opt-out
plaintext path used by tests. See
``docs/superpowers/specs/2026-04-27-adk-credential-service-design.md`` for
the wire format and threat model.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_service.base_credential_service import BaseCredentialService
from kurrentdbclient import StreamState

from . import _serialization
from ._streams import for_credentials
from .credential_cipher import CredentialCipher, CredentialContext
from .events import CredentialSaved

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.agents.callback_context import CallbackContext
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBCredentialService(BaseCredentialService):
    """KurrentDB-backed credential service keyed by ``AuthConfig.credential_key``.

    Args:
        client: Async KurrentDB client.
        cipher: Encrypts the ``AuthCredential`` payload before it lands on
            the stream. Required — no plaintext default. Use
            :class:`AesGcmCredentialCipher` for production and
            :class:`NullCredentialCipher` only for tests or explicit opt-out.
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        cipher: CredentialCipher,
    ) -> None:
        self._client = client
        self._cipher = cipher

    async def save_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> None:
        app_name, user_id = _scope(callback_context)
        key = auth_config.credential_key
        if key is None:
            raise ValueError("auth_config.credential_key is required")
        cred = auth_config.exchanged_auth_credential
        if cred is None:
            raise ValueError("auth_config.exchanged_auth_credential is required")

        ctx = CredentialContext(app_name=app_name, user_id=user_id, credential_key=key)
        plaintext = cred.model_dump_json(exclude_none=True).encode("utf-8")
        wire = self._cipher.encrypt(plaintext, ctx)

        event = CredentialSaved(
            credential_key=key,
            credential=base64.b64encode(wire).decode("ascii"),
            timestamp=datetime.now(timezone.utc),
        )
        stream = for_credentials(app_name, user_id)
        await self._client.append_to_stream(
            stream,
            events=[_serialization.serialize(event)],
            current_version=StreamState.ANY,
        )

    async def load_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> AuthCredential | None:
        raise NotImplementedError  # implemented in Task 7


def _scope(callback_context: CallbackContext) -> tuple[str, str]:
    """Extract (app_name, user_id) from the callback context.

    Mirrors the access path used by ADK's ``InMemoryCredentialService``.
    """
    invocation = callback_context._invocation_context  # noqa: SLF001
    return invocation.app_name, invocation.user_id
```

- [ ] **Step 5: Test still fails — `load_credential` raises `NotImplementedError`**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_service.py -v -k test_round_trip`
Expected: FAIL — `NotImplementedError` from `load_credential` (we expect this to flip green in Task 7).

- [ ] **Step 6: Commit save_credential without load**

```bash
git add google-adk/python/kurrent_google_adk/credential_service.py google-adk/python/tests/test_credential_service.py
git commit -m "feat(adk): KurrentDBCredentialService.save_credential (DEV-1481)"
```

---

## Task 7: Implement `KurrentDBCredentialService.load_credential`

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/credential_service.py`

- [ ] **Step 1: Replace `load_credential`'s stub with the real implementation**

Edit `credential_service.py` and replace the `load_credential` body:

```python
    async def load_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> AuthCredential | None:
        app_name, user_id = _scope(callback_context)
        key = auth_config.credential_key
        if key is None:
            raise ValueError("auth_config.credential_key is required")

        ctx = CredentialContext(app_name=app_name, user_id=user_id, credential_key=key)
        stream = for_credentials(app_name, user_id)

        from kurrentdbclient.exceptions import NotFoundError

        try:
            recorded = self._client.read_stream(stream, backwards=True)
        except NotFoundError:
            return None

        async for record in recorded:
            event = _serialization.deserialize(record)
            if not isinstance(event, CredentialSaved):
                continue
            if event.credential_key != key:
                continue
            wire = base64.b64decode(event.credential)
            plaintext = self._cipher.decrypt(wire, ctx)
            return AuthCredential.model_validate_json(plaintext)

        return None
```

- [ ] **Step 2: Run the round-trip test and verify it passes**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_service.py -v -k test_round_trip`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/kurrent_google_adk/credential_service.py
git commit -m "feat(adk): KurrentDBCredentialService.load_credential (DEV-1481)"
```

---

## Task 8: Service contract tests (DEV-1481 acceptance)

**Files:**
- Modify: `google-adk/python/tests/test_credential_service.py`

- [ ] **Step 1: Write the four DEV-1481 acceptance tests + AES-GCM round-trip**

Append to `google-adk/python/tests/test_credential_service.py`:

```python
import os


class TestSaveAndLoadAesGcm:
    async def test_round_trip(self, kurrentdb_client: AsyncKurrentDBClient) -> None:
        cipher = AesGcmCredentialCipher(keys=[os.urandom(32)])
        service = KurrentDBCredentialService(kurrentdb_client, cipher=cipher)
        app, user = _ids()
        cfg = _auth_config()

        await service.save_credential(cfg, _ctx(app, user))  # type: ignore[arg-type]
        loaded = await service.load_credential(cfg, _ctx(app, user))  # type: ignore[arg-type]

        assert loaded is not None
        assert loaded.http.credentials.password == "pw-drive.readonly"

    async def test_persisted_event_payload_is_not_plaintext(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Sanity-check the threat model: with AES-GCM, the credential
        does not appear in cleartext on the stream.
        """
        cipher = AesGcmCredentialCipher(keys=[os.urandom(32)])
        service = KurrentDBCredentialService(kurrentdb_client, cipher=cipher)
        app, user = _ids()
        cfg = _auth_config()
        await service.save_credential(cfg, _ctx(app, user))  # type: ignore[arg-type]

        from kurrent_google_adk._streams import for_credentials
        stream = for_credentials(app, user)
        async for record in kurrentdb_client.read_stream(stream):
            assert b"alice" not in record.data
            assert b"pw-drive.readonly" not in record.data


class TestMostRecentWins:
    async def test_second_save_shadows_first(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBCredentialService(kurrentdb_client, cipher=NullCredentialCipher())
        app, user = _ids()
        ctx = _ctx(app, user)

        first = _auth_config(scope="v1")
        await service.save_credential(first, ctx)  # type: ignore[arg-type]

        second = _auth_config(scope="v1")  # same credential_key, different secret
        second.exchanged_auth_credential.http.credentials.password = "newer"
        await service.save_credential(second, ctx)  # type: ignore[arg-type]

        loaded = await service.load_credential(first, ctx)  # type: ignore[arg-type]
        assert loaded.http.credentials.password == "newer"


class TestIndependentKeys:
    async def test_distinct_credential_keys_dont_shadow(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBCredentialService(kurrentdb_client, cipher=NullCredentialCipher())
        app, user = _ids()
        ctx = _ctx(app, user)

        a = _auth_config(scope="alpha")
        b = _auth_config(scope="beta")
        await service.save_credential(a, ctx)  # type: ignore[arg-type]
        await service.save_credential(b, ctx)  # type: ignore[arg-type]

        loaded_a = await service.load_credential(a, ctx)  # type: ignore[arg-type]
        loaded_b = await service.load_credential(b, ctx)  # type: ignore[arg-type]
        assert loaded_a.http.credentials.password == "pw-alpha"
        assert loaded_b.http.credentials.password == "pw-beta"


class TestMissing:
    async def test_missing_stream_returns_none(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBCredentialService(kurrentdb_client, cipher=NullCredentialCipher())
        app, user = _ids()
        cfg = _auth_config()
        loaded = await service.load_credential(cfg, _ctx(app, user))  # type: ignore[arg-type]
        assert loaded is None

    async def test_missing_key_in_existing_stream_returns_none(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBCredentialService(kurrentdb_client, cipher=NullCredentialCipher())
        app, user = _ids()
        ctx = _ctx(app, user)

        await service.save_credential(_auth_config(scope="written"), ctx)  # type: ignore[arg-type]
        loaded = await service.load_credential(_auth_config(scope="never-written"), ctx)  # type: ignore[arg-type]
        assert loaded is None
```

- [ ] **Step 2: Run the new tests and verify they pass**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_service.py -v`
Expected: PASS — all tests in this file (1 from Task 6 + 7 added here).

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/tests/test_credential_service.py
git commit -m "test(adk): DEV-1481 credential service acceptance tests"
```

---

## Task 9: AAD-binding cross-user defence test

**Files:**
- Modify: `google-adk/python/tests/test_credential_service.py`

This guards the design choice: a database-level attacker who copies a `CredentialSaved` event from user A's stream to user B's stream cannot get user B's load to succeed. Without AAD binding, this would silently work.

- [ ] **Step 1: Write the failing-relocation test**

Append to `google-adk/python/tests/test_credential_service.py`:

```python
class TestAadBinding:
    async def test_relocating_event_to_other_user_breaks_decrypt(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """A ciphertext lifted from user A and replanted under user B
        must fail to decrypt because AAD includes the user_id."""
        cipher = AesGcmCredentialCipher(keys=[os.urandom(32)])
        service = KurrentDBCredentialService(kurrentdb_client, cipher=cipher)
        app, alice = _ids()
        _, bob = _ids()
        cfg = _auth_config()

        # Write under alice.
        await service.save_credential(cfg, _ctx(app, alice))  # type: ignore[arg-type]

        # Copy alice's event verbatim into bob's stream.
        from kurrent_google_adk._streams import for_credentials
        from kurrentdbclient import NewEvent, StreamState
        alice_stream = for_credentials(app, alice)
        async for record in kurrentdb_client.read_stream(alice_stream, backwards=True):
            replayed = NewEvent(id=record.id, type=record.type, data=record.data, metadata=record.metadata)
            await kurrentdb_client.append_to_stream(
                for_credentials(app, bob),
                events=[replayed],
                current_version=StreamState.ANY,
            )
            break

        # Bob's load must NOT silently succeed; it must raise (decrypt failure).
        with pytest.raises(Exception):
            await service.load_credential(cfg, _ctx(app, bob))  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the test and verify it passes**

Run: `uv run --project google-adk/python pytest google-adk/python/tests/test_credential_service.py::TestAadBinding -v`
Expected: PASS — the AAD mismatch raises an `InvalidTag` from `cryptography`.

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/tests/test_credential_service.py
git commit -m "test(adk): AAD binding rejects cross-user ciphertext relocation (DEV-1481)"
```

---

## Task 10: Update `__init__.py` exports

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/__init__.py`

`KurrentDBCredentialService` is already exported per the existing stub. Add the cipher exports so users can wire them without reaching into `credential_cipher` directly.

- [ ] **Step 1: Edit the package init**

Edit `google-adk/python/kurrent_google_adk/__init__.py`. Add the imports near the existing `KurrentDBCredentialService` import (alphabetical order if the file uses it; otherwise group with the other credential symbols):

```python
from .credential_cipher import (
    AesGcmCredentialCipher,
    CredentialCipher,
    CredentialContext,
    NullCredentialCipher,
)
from .credential_service import KurrentDBCredentialService
```

Add to `__all__` (preserving the existing list's ordering convention):

```python
__all__ = [
    # ... existing entries ...
    "AesGcmCredentialCipher",
    "CredentialCipher",
    "CredentialContext",
    "KurrentDBCredentialService",
    "NullCredentialCipher",
]
```

- [ ] **Step 2: Verify the public imports resolve**

Run: `uv run --project google-adk/python python -c "from kurrent_google_adk import KurrentDBCredentialService, AesGcmCredentialCipher, NullCredentialCipher, CredentialCipher, CredentialContext; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/kurrent_google_adk/__init__.py
git commit -m "feat(adk): export credential cipher symbols (DEV-1481)"
```

---

## Task 11: Update DESIGN.md §7.4 + §13

**Files:**
- Modify: `google-adk/python/DESIGN.md`

- [ ] **Step 1: Replace §7.4**

Find the heading `### 7.4 KurrentDBCredentialService` in `google-adk/python/DESIGN.md` and replace its body with:

```markdown
Implements `BaseCredentialService.load_credential` / `save_credential`. Keys
off `auth_config.credential_key` (stable string ADK derives from auth scheme
+ scopes). Credentials are written to `Credentials-{app_name}-{user_id}` as
ADK-specific `CredentialSaved` events keyed by the credential key; read is a
backward scan returning the most-recent matching key, or `None` if absent.

A pluggable `CredentialCipher` is required at construction; there is no
plaintext default. Two implementations ship in the package:

- `AesGcmCredentialCipher` (recommended): AES-256-GCM with AAD binding to
  `(app_name, user_id, credential_key)`, so a ciphertext blob lifted from
  one user's stream and replanted under another user's stream fails to
  decrypt. Versioned wire format with a `key_id` byte for in-place key
  rotation. Requires the `cryptography` package — install via
  `pip install kurrent-google-adk[crypto]`.
- `NullCredentialCipher`: explicit no-encryption path for tests and the
  small set of users who genuinely don't want encryption. Same outer wire
  container as the AES path so the on-stream shape is uniform regardless
  of cipher choice.

See `docs/superpowers/specs/2026-04-27-adk-credential-service-design.md`
for the wire format, AAD construction, and rotation procedure.
```

- [ ] **Step 2: Close open question 2 in §13**

Find `2. **Credential encryption.**` in §13 and replace its bullet with:

```markdown
2. **Credential encryption.** ~~Pluggable `CredentialCipher` interface — add in v1 as a no-op default, or defer to v2?~~ **Resolved.** Pluggable `CredentialCipher` is part of v1; AES-256-GCM with AAD binding is the recommended default. See §7.4 and `docs/superpowers/specs/2026-04-27-adk-credential-service-design.md`.
```

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/DESIGN.md
git commit -m "docs(adk): close credential encryption open question (DEV-1481)"
```

---

## Task 12: Update `SCHEMA.md` (and `SCHEMA_v2.md` if separate) note on `CredentialSaved`

**Files:**
- Modify: `schema/SCHEMA.md`
- Modify (if it tracks the same content): `schema/SCHEMA_v2.md`

`SCHEMA.md` already calls out the ADK Credentials stream. Add a one-line note that the payload is encrypted (cipher-self-describing wire blob) under the recommended configuration.

- [ ] **Step 1: Edit `SCHEMA.md`**

Find the line containing `Credentials-{app_name}-{user_id}` in `schema/SCHEMA.md`. Update the table cell or follow-on note to read (preserve surrounding markdown structure):

```markdown
| `Credentials-{app_name}-{user_id}` | ADK | Tool OAuth credentials. Payload is a base64-encoded, cipher-self-describing wire blob (see `kurrent_google_adk/DESIGN.md` §7.4 — recommended cipher is AES-256-GCM with AAD binding). |
```

Find the existing "ADK OAuth credentials" callout in the same file (around the credential extension-slug section) and append:

```markdown
- **ADK OAuth credentials.** Stored in `Credentials-...` as `CredentialSaved` events; no AFW equivalent yet. The `credential` field on the event is a base64-encoded ciphertext blob whose wire format is owned by the writing cipher (see `kurrent_google_adk/DESIGN.md` §7.4).
```

- [ ] **Step 2: Mirror the change in `SCHEMA_v2.md` if applicable**

If `schema/SCHEMA_v2.md` carries a duplicate `Credentials-{app_name}-{user_id}` row (line 49 at time of writing), apply the same wording change. If the v2 doc only lists the row in the table without a follow-on callout, only update the row.

- [ ] **Step 3: Commit**

```bash
git add schema/SCHEMA.md schema/SCHEMA_v2.md
git commit -m "docs(schema): note CredentialSaved payload is cipher-encoded (DEV-1481)"
```

---

## Task 13: Final sanity run

**Files:** none changed; this is a check.

- [ ] **Step 1: Full ADK Python test suite**

Run: `uv run --project google-adk/python pytest -q`
Expected: green. The new files are `tests/test_credential_cipher.py` and `tests/test_credential_service.py`; the existing suite (`test_codec.py`, `test_session_service.py`, `test_memory_service.py`, etc.) must still pass — Task 1's serialization registry change is the only cross-cutting edit.

- [ ] **Step 2: Lint check**

Run: `uv run --project google-adk/python ruff check google-adk/python/kurrent_google_adk google-adk/python/tests`
Expected: no findings.

- [ ] **Step 3: Verify package surface**

Run: `uv run --project google-adk/python python -c "import kurrent_google_adk as p; assert {'KurrentDBCredentialService', 'AesGcmCredentialCipher', 'NullCredentialCipher', 'CredentialCipher', 'CredentialContext'} <= set(p.__all__); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: No commit needed** — this task is a verification gate.

---

## Follow-up (out of this plan): live OAuth sample

DEV-1481 acceptance includes "Update `samples/basic_agent` (or new sample) to exercise an OAuth-bound tool and show the credential landing in KurrentDB." A real OAuth flow needs provider setup (Google OAuth client credentials + browser-based consent) and is genuinely manual rather than CI-testable.

**Recommendation:** ship this plan first (the integration tests in Task 8 + Task 9 cover the storage contract end-to-end), then open a follow-up issue for the OAuth-tool sample. The follow-up issue should:

- Add a `samples/oauth_agent/` based on `samples/basic_agent/`.
- Wire an ADK tool that requires OAuth (e.g. Google Drive read-only).
- Wire `KurrentDBCredentialService(client, cipher=AesGcmCredentialCipher.from_env(...))` (the `from_env` constructor is itself open-question 4 in the spec — implement if accepted, otherwise have the sample read a base64 key from an env var directly).
- Ship a README walkthrough (request OAuth consent, watch credential land in `Credentials-` stream, restart, watch the cached credential get re-loaded).

---

## Self-review summary

- **Spec coverage:** all spec sections map to tasks — `CredentialSaved` (T1), `CredentialContext` + protocol (T2), `NullCredentialCipher` (T3), `[crypto]` extra (T4), AES-GCM cipher with wire format + AAD + rotation + errors (T5), service `save`/`load` (T6, T7), DEV-1481 acceptance tests (T8), AAD relocation defence (T9), exports (T10), DESIGN.md update (T11), SCHEMA note (T12). The OAuth sample is split into a follow-up with rationale documented in §Follow-up.
- **Type consistency:** event field names (`credential_key`, `credential`, `timestamp`), cipher methods (`encrypt`, `decrypt`), context field names (`app_name`, `user_id`, `credential_key`), error class names (`MalformedCiphertextError`, `UnsupportedCipherVersionError`, `UnknownKeyIdError`) match across tasks.
- **No placeholders** — every step gives the exact code or command an engineer needs.
