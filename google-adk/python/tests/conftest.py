"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from kurrentdbclient import AsyncKurrentDBClient

# Default points at the docker-compose setup in this folder; override via env
# var for CI or remote clusters.
_DEFAULT_CONNECTION_STRING = "kurrentdb://localhost:2113?Tls=false"


def _connection_string() -> str:
    return os.environ.get("KURRENTDB_CONNECTION_STRING", _DEFAULT_CONNECTION_STRING)


def _kurrentdb_available() -> bool:
    """Best-effort probe: can we reach ``/health/live``?"""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(_connection_string().replace("kurrentdb://", "http://"))
    host = parsed.hostname or "localhost"
    port = parsed.port or 2113
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def kurrentdb_available() -> bool:
    return _kurrentdb_available()


@pytest_asyncio.fixture
async def kurrentdb_client(kurrentdb_available: bool) -> AsyncKurrentDBClient:
    """Live client; the whole test is skipped if no KurrentDB is reachable."""
    if not kurrentdb_available:
        pytest.skip(
            "KurrentDB unreachable; start it with `docker compose up -d` in the "
            "package directory, or set KURRENTDB_CONNECTION_STRING."
        )
    client = AsyncKurrentDBClient(_connection_string())
    try:
        yield client
    finally:
        await client.close()
