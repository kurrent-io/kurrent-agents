"""Kurrent integration for Google ADK (Python).

Event-sourced session persistence, memory, artifacts, credentials, and
evaluation for Google's Agent Development Kit, backed by KurrentDB.

See ``DESIGN.md`` in this directory for the full design spec, and
``schema/SCHEMA_v2.md`` at the repo root for the canonical event vocabulary
shared with the Microsoft Agent Framework integrations.
"""

from . import client
from ._revisions import StaleSessionError
from .artifact_service import KurrentDBArtifactService
from .credential_cipher import (
    AesGcmCredentialCipher,
    CredentialCipher,
    CredentialContext,
    NullCredentialCipher,
)
from .credential_service import KurrentDBCredentialService
from .memory_service import KurrentDBMemoryService
from .session_service import KurrentDBSessionService

__all__ = [
    "AesGcmCredentialCipher",
    "CredentialCipher",
    "CredentialContext",
    "KurrentDBArtifactService",
    "KurrentDBCredentialService",
    "KurrentDBMemoryService",
    "KurrentDBSessionService",
    "NullCredentialCipher",
    "StaleSessionError",
    "client",
]
