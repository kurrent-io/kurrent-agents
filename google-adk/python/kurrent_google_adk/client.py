"""Helpers for constructing a KurrentDB client.

Users who already have a configured ``AsyncKurrentDBClient`` can pass it
directly to the service constructors; these helpers exist for convenience and
for samples.
"""

from __future__ import annotations

from kurrentdbclient import AsyncKurrentDBClient


def from_connection_string(connection_string: str) -> AsyncKurrentDBClient:
    """Return a new ``AsyncKurrentDBClient`` from a ``kurrentdb://`` URI."""
    return AsyncKurrentDBClient(connection_string)
