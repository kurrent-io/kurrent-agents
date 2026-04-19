"""Kurrent integration for the Strands Agents SDK (Python).

Canonical-event persistence for Strands sessions, sharing the event schema
with the Google ADK and Microsoft Agent Framework integrations in this
monorepo. See ``DESIGN.md`` in this directory.
"""

from . import client
from .memory import KurrentDBAgentMemory
from .session_manager import KurrentDBSessionManager

__all__ = [
    "KurrentDBAgentMemory",
    "KurrentDBSessionManager",
    "client",
]
