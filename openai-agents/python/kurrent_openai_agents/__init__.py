"""Kurrent integration for the OpenAI Agents SDK (Python).

Drop-in ``Session`` implementation backed by KurrentDB, sharing the canonical
event schema with the Google ADK, Microsoft Agent Framework, and Strands
integrations in this monorepo. See ``DESIGN.md`` in this directory.
"""

from . import client
from .session import KurrentDBSession

__all__ = [
    "KurrentDBSession",
    "client",
]
