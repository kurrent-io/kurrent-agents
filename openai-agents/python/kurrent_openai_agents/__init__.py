"""Kurrent integration for the OpenAI Agents SDK (Python).

Drop-in ``Session`` implementation backed by KurrentDB, sharing the canonical
event schema with the Google ADK, Microsoft Agent Framework, Strands, and
Claude Agent SDK integrations in this monorepo. See ``DESIGN.md``.
"""

from . import client
from ._openai_events import OPENAI_EXTENSION_KEY, OpenAIItem
from .session import KurrentDBSession

__all__ = [
    "KurrentDBSession",
    "OpenAIItem",
    "OPENAI_EXTENSION_KEY",
    "client",
]
