"""Integration tests for ``KurrentDBCredentialService`` against a live KurrentDB."""
from __future__ import annotations

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
from kurrent_google_adk.credential_cipher import NullCredentialCipher


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
