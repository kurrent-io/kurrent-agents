"""Kurrent integration for the Claude Agent SDK (Python).

Mirrors CLI transcript entries from the SDK's ``SessionStore`` adapter hook to
KurrentDB. See ``DESIGN.md``.

Canonical schema types live in :mod:`kurrent_agent_schema`; import those
directly. Only :class:`ClaudeSDKEntry` (the verbatim envelope) and
:data:`CLAUDE_SDK_EXTENSION_KEY` are re-exported here.
"""

from . import client, decompose
from .decompose import decompose_entry, decompose_stream
from .events import CLAUDE_SDK_EXTENSION_KEY, ClaudeSDKEntry
from .session_store import KurrentDBSessionStore

__all__ = [
    "CLAUDE_SDK_EXTENSION_KEY",
    "ClaudeSDKEntry",
    "KurrentDBSessionStore",
    "client",
    "decompose",
    "decompose_entry",
    "decompose_stream",
]
