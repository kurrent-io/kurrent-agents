"""Helpers for constructing a (sync) KurrentDB client.

Strands' ``SessionManager`` callbacks are invoked synchronously, so this
integration uses ``KurrentDBClient`` (not the async variant).
"""

from __future__ import annotations

from kurrentdbclient import KurrentDBClient


def from_connection_string(connection_string: str) -> KurrentDBClient:
    """Return a new sync ``KurrentDBClient`` from a ``kurrentdb://`` URI."""
    return KurrentDBClient(connection_string)
