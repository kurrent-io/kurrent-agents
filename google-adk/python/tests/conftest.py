"""Shared fixtures — delegates to kurrent-agents-testing."""

from __future__ import annotations

from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
