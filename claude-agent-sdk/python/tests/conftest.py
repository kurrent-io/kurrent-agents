"""Shared fixtures — delegates to kurrent-agents-testing."""

from __future__ import annotations

# The async fixture in kurrent-agents-testing already tolerates the lazy-
# connection close() behaviour this suite requires — no local shim needed.
from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
