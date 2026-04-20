"""Shared fixtures — delegates to kurrent-agents-testing.

ADK needs ``projections=All`` (the OTEL projection relies on secondary indexes),
so this package overrides the shared ``kurrentdb_container`` fixture with
pytest's standard name-based override mechanism.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kurrent_agents_testing.container import KurrentDBContainer
from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    reuse_enabled,
)


@pytest.fixture(scope="session")
def kurrentdb_container() -> Iterator[KurrentDBContainer]:
    c = KurrentDBContainer(projections="All", reuse=reuse_enabled()).start()
    try:
        yield c
    finally:
        if not reuse_enabled():
            c.stop()
