"""Helpers for constructing an async KurrentDB client.

The Claude Agent SDK's ``SessionStore`` protocol is async-native, so we use
``AsyncKurrentDBClient``.
"""

from __future__ import annotations

from kurrentdbclient import AsyncKurrentDBClient


def from_connection_string(connection_string: str) -> AsyncKurrentDBClient:
    """Return a new ``AsyncKurrentDBClient`` from a ``kurrentdb://`` URI."""
    return AsyncKurrentDBClient(connection_string)
