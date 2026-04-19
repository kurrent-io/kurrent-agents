"""Shared test fixtures."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from kurrentdbclient import AsyncKurrentDBClient

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


@pytest_asyncio.fixture
async def kurrentdb_client() -> AsyncKurrentDBClient:
    if not _kurrentdb_reachable():
        pytest.skip(
            "KurrentDB unreachable; start with `docker compose up -d` or set "
            "KURRENTDB_CONNECTION_STRING."
        )
    client = AsyncKurrentDBClient(_connection_string())
    try:
        yield client
    finally:
        # Tolerate tests that never made a call — the connection is lazy,
        # so close() raises on a never-used client. Not a real failure.
        try:
            await client.close()
        except Exception:
            pass
