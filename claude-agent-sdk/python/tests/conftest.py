"""Shared fixtures — delegates to kurrent-agents-testing.

The async fixture in kurrent-agents-testing already tolerates the lazy-
connection close() behaviour this suite requires — no local shim needed.
The container / connection-string fixtures come in as transitive dependencies
of ``async_kurrentdb_client``; ``__all__`` marks them as intentional
re-exports so static checkers don't mistake them for unused imports.
"""

from __future__ import annotations

from kurrent_agents_testing.fixtures import (
    async_kurrentdb_client as kurrentdb_client,
)
from kurrent_agents_testing.fixtures import (
    kurrentdb_connection_string,
    kurrentdb_container,
)

__all__ = [
    "kurrentdb_client",
    "kurrentdb_connection_string",
    "kurrentdb_container",
]
