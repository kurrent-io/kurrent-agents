"""``KurrentDBCredentialService`` — see ``DESIGN.md`` §7.4.

Implements ``google.adk.auth.credential_service.BaseCredentialService`` on a
KurrentDB ``Credentials-{app}-{user}`` stream. ADK-specific; no AFW or
Strands analogue, so the event type ``CredentialSaved`` lives outside the
canonical vocabulary and is never emitted by other integrations.

Encryption-at-rest is deferred; users place KurrentDB behind a storage-layer
encryption solution for v1. A pluggable ``CredentialCipher`` interface can
be added later without a breaking change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_service.base_credential_service import BaseCredentialService

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.agents.callback_context import CallbackContext
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBCredentialService(BaseCredentialService):
    """KurrentDB-backed credential service keyed by ``AuthConfig.get_credential_key()``."""

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client

    async def load_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> AuthCredential | None:
        raise NotImplementedError

    async def save_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> None:
        raise NotImplementedError
