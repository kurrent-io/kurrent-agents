"""``KurrentDBEvalSetsManager`` — see ``DESIGN.md`` §7.6.

Stores eval case definitions as events in KurrentDB. Canonical stream naming
TBD; likely ``EvalSet-{app_name}-{eval_set_id}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.evaluation.eval_sets_manager import EvalSetsManager

if TYPE_CHECKING:  # pragma: no cover
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBEvalSetsManager(EvalSetsManager):
    """KurrentDB-backed eval-sets manager (stub)."""

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client

    # NOTE: the EvalSetsManager abstract surface is populated once the upstream
    # ADK interface is stable for this package version. Current implementation
    # deferred; see DESIGN.md §7.6.
