"""Integration tests for ``KurrentDBCredentialService`` against a live KurrentDB."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from google.adk.auth.auth_credential import (
    AuthCredential,
    AuthCredentialTypes,
    HttpAuth,
    HttpCredentials,
)
from google.adk.auth.auth_schemes import AuthSchemeType
from google.adk.auth.auth_tool import AuthConfig
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_google_adk import KurrentDBCredentialService
from kurrent_google_adk._streams import for_credentials
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

        stream = for_credentials(app, user)
        async for record in await kurrentdb_client.read_stream(stream):
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
