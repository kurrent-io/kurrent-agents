"""``KurrentDBMemoryService`` — see ``DESIGN.md`` §7.2.

Implements ``google.adk.memory.BaseMemoryService`` backed by the canonical
``AgentMemory-{app}-{user}`` stream (``SCHEMA.md`` §3.6). Each retained entry
is a ``FactRetained`` event; default ``search_memory`` returns all entries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.events.event import Event
    from google.adk.sessions.session import Session
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBMemoryService(BaseMemoryService):
    """KurrentDB-backed memory service (per-app, per-user scope)."""

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client

    async def add_session_to_memory(self, session: Session) -> None:
        raise NotImplementedError

    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: Sequence[Event],
        session_id: str | None = None,
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        raise NotImplementedError

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        raise NotImplementedError

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        raise NotImplementedError
