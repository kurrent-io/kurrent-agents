"""Cross-session fact memory backed by KurrentDB.

Mirrors the `KurrentDBMemoryService` from the ADK integration — same
canonical stream (``AgentMemory-{app}-{user}``), same ``FactRetained`` event.
A Strands agent's retained facts are visible to ADK agents for the same
(app, user) scope, and vice versa.

Strands has no built-in memory-service abstraction to extend, so this is a
plain class the sample wires into tool callbacks via closure. v1 search is
baseline (returns every retained fact, newest-first, query ignored). Swap
in your own class for richer retrieval.

Sync — matches the rest of the Strands integration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kurrent_agent_schema import FactRetained
from kurrentdbclient import StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from ._stream_names import for_memory

if TYPE_CHECKING:  # pragma: no cover
    from kurrentdbclient import KurrentDBClient


class KurrentDBAgentMemory:
    """Per-app, per-user fact memory persisted in KurrentDB."""

    def __init__(
        self,
        client: KurrentDBClient,
        *,
        app_name: str,
        user_id: str,
    ) -> None:
        self._client = client
        self._app_name = app_name
        self._user_id = user_id
        self._stream = for_memory(app_name, user_id)

    def retain(self, fact: str) -> None:
        """Append one ``FactRetained`` event to the memory stream.

        Empty / whitespace-only facts are silently dropped.
        """
        if not fact or not fact.strip():
            return
        event = FactRetained(
            fact=fact.strip(),
            retained_at=datetime.now(UTC),
        )
        self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(event)],
            current_version=StreamState.ANY,
        )

    def recall(self, query: str | None = None) -> list[str]:
        """Return every retained fact, newest-first. ``query`` is ignored in v1."""
        del query
        try:
            recorded = self._client.get_stream(self._stream, backwards=True)
        except NotFoundError:
            return []
        facts: list[str] = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if not isinstance(event, FactRetained):
                continue
            if event.fact.strip():
                facts.append(event.fact)
        return facts
