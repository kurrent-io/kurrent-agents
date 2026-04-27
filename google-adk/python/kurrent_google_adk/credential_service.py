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
from datetime import UTC, datetime
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
            timestamp=datetime.now(UTC),
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
    invocation = callback_context._invocation_context
    return invocation.app_name, invocation.user_id
