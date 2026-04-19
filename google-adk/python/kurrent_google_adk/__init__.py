"""Kurrent integration for Google ADK (Python).

Event-sourced session persistence, memory, artifacts, credentials, and
evaluation for Google's Agent Development Kit, backed by KurrentDB.

See ``DESIGN.md`` in this directory for the full design spec, and
``schema/SCHEMA.md`` at the repo root for the canonical event vocabulary
shared with the Microsoft Agent Framework integrations.
"""

from . import client
from ._revisions import StaleSessionError
from .artifact_service import KurrentDBArtifactService
from .credential_service import KurrentDBCredentialService
from .memory_service import KurrentDBMemoryService
from .session_service import KurrentDBSessionService

__all__ = [
    "KurrentDBArtifactService",
    "KurrentDBCredentialService",
    "KurrentDBMemoryService",
    "KurrentDBSessionService",
    "StaleSessionError",
    "client",
]
