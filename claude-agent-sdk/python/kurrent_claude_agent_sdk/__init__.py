"""Kurrent integration for the Claude Agent SDK (Python).

Mirrors CLI transcript entries from the SDK's ``SessionStore`` adapter hook to
KurrentDB. See ``DESIGN.md``.
"""

from . import client, decompose
from .decompose import decompose_entry, decompose_stream
from .session_store import KurrentDBSessionStore

__all__ = [
    "KurrentDBSessionStore",
    "client",
    "decompose",
    "decompose_entry",
    "decompose_stream",
]
