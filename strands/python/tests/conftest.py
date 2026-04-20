"""Shared fixtures — delegates to kurrent-agents-testing.

Strands' SessionManager is sync, so this suite uses the sync client fixture.
"""

from __future__ import annotations

from kurrent_agents_testing.fixtures import (  # noqa: F401
    kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
