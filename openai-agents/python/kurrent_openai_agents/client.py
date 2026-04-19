"""Helpers for constructing a (async) KurrentDB client.

The OpenAI Agents SDK is async-native, so this integration uses
``AsyncKurrentDBClient``.
"""

from __future__ import annotations

from kurrentdbclient import AsyncKurrentDBClient


def from_connection_string(connection_string: str) -> AsyncKurrentDBClient:
    """Return a new async ``AsyncKurrentDBClient`` from a ``kurrentdb://`` URI."""
    return AsyncKurrentDBClient(connection_string)
