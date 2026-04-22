"""Shared fixtures — delegates to kurrent-agents-testing.

``__all__`` lists the fixtures we re-export so pytest's resolver can chain
``kurrentdb_client → kurrentdb_connection_string → kurrentdb_container``
without ruff's isort fixer treating the intermediate fixtures as unused.
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
