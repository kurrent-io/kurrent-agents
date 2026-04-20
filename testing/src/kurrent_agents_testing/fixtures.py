"""Session-scoped pytest fixtures for a KurrentDB Testcontainer.

Consumers typically import the fixtures they need into their own
``conftest.py`` via::

    from kurrent_agents_testing.fixtures import async_kurrentdb_client  # noqa: F401

If a package needs a different container configuration (e.g. a non-default
``projections`` value), it can override the ``kurrentdb_container`` fixture
in its own ``conftest.py`` via pytest's standard name-based fixture-override
mechanism — no package does today.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from kurrentdbclient import AsyncKurrentDBClient, KurrentDBClient

from .container import KurrentDBContainer


def reuse_enabled() -> bool:
    """``True`` outside of CI — enables testcontainers container reuse.

    ``"yes"`` is included in the CI-detection set because some CI systems
    (e.g. certain Jenkins/GitLab setups) set ``CI=yes`` rather than the more
    common ``CI=1`` or ``CI=true``.
    """
    return os.environ.get("CI", "").lower() not in ("1", "true", "yes")


@pytest.fixture(scope="session")
def kurrentdb_container() -> Iterator[KurrentDBContainer]:
    c = KurrentDBContainer(reuse=reuse_enabled()).start()
    try:
        yield c
    finally:
        # Only skip teardown when the container is actually labelled for reuse
        # (older testcontainers versions lack ``with_reuse``; see
        # ``KurrentDBContainer._reuse_applied``). Otherwise always stop so we
        # don't leak orphan containers across ``pytest`` invocations.
        if not getattr(c, "_reuse_applied", False):
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
