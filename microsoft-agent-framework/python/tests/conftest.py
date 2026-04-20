"""Shared fixtures — delegates to kurrent-agents-testing."""

from __future__ import annotations

# Re-exported so tests referencing ``kurrentdb_client`` still work. The async
# flavour is the one this suite has always used.
from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
