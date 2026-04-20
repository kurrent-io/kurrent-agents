"""Session-scoped pytest fixtures for a KurrentDB Testcontainer.

Consumers typically import the fixtures they need into their own
``conftest.py`` via::

    from kurrent_agents_testing.fixtures import async_kurrentdb_client  # noqa: F401

The shared ``kurrentdb_container`` fixture can be overridden per package
(e.g. ``google-adk`` needs ``projections="All"``) using pytest's standard
fixture-override mechanism.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from kurrentdbclient import AsyncKurrentDBClient, KurrentDBClient

from .container import KurrentDBContainer


def reuse_enabled() -> bool:
    """``True`` outside of CI — enables testcontainers container reuse."""
    return os.environ.get("CI", "").lower() not in ("1", "true", "yes")


@pytest.fixture(scope="session")
def kurrentdb_container() -> Iterator[KurrentDBContainer]:
    c = KurrentDBContainer(reuse=reuse_enabled()).start()
    try:
        yield c
    finally:
        if not reuse_enabled():
            c.stop()


@pytest.fixture(scope="session")
def kurrentdb_connection_string(kurrentdb_container: KurrentDBContainer) -> str:
    return kurrentdb_container.connection_string()


@pytest.fixture
def kurrentdb_client(kurrentdb_connection_string: str) -> Iterator[KurrentDBClient]:
    client = KurrentDBClient(kurrentdb_connection_string)
    try:
        yield client
    finally:
        client.close()


@pytest_asyncio.fixture
async def async_kurrentdb_client(
    kurrentdb_connection_string: str,
) -> AsyncIterator[AsyncKurrentDBClient]:
    client = AsyncKurrentDBClient(kurrentdb_connection_string)
    try:
        yield client
    finally:
        # The async client lazily opens the connection on first call, so
        # close() raises when the test never used it. Mirrors the pre-existing
        # tolerance in claude-agent-sdk's conftest.
        try:
            await client.close()
        except Exception:
            pass
