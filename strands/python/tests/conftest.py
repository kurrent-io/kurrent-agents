"""Shared test fixtures."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest
from kurrentdbclient import KurrentDBClient

_DEFAULT_CONNECTION_STRING = "kurrentdb://localhost:2113?Tls=false"


def _connection_string() -> str:
    return os.environ.get("KURRENTDB_CONNECTION_STRING", _DEFAULT_CONNECTION_STRING)


def _kurrentdb_reachable() -> bool:
    parsed = urlparse(_connection_string().replace("kurrentdb://", "http://"))
    host = parsed.hostname or "localhost"
    port = parsed.port or 2113
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def kurrentdb_client() -> KurrentDBClient:
    """Live sync client; test skipped when KurrentDB is unreachable."""
    if not _kurrentdb_reachable():
        pytest.skip(
            "KurrentDB unreachable; start with `docker compose up -d` or set "
            "KURRENTDB_CONNECTION_STRING."
        )
    client = KurrentDBClient(_connection_string())
    try:
        yield client
    finally:
        client.close()
