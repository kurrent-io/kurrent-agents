"""``KurrentDBSessionService`` — see ``DESIGN.md`` §7.1.

Implements ``google.adk.sessions.BaseSessionService`` backed by KurrentDB.
Writes canonical events per ``SCHEMA.md``; preserves non-canonical ADK state
in ``extensions.adk``; routes ``state_delta`` entries to the correct stream
by prefix; handles rewind/compaction meta-events; enforces optimistic
concurrency via revision tracking (``DESIGN.md`` §8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.sessions.base_session_service import BaseSessionService, GetSessionConfig, ListSessionsResponse

from ._revisions import RevisionTracker

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.events.event import Event
    from google.adk.sessions.session import Session
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBSessionService(BaseSessionService):
    """KurrentDB-backed session service.

    Args:
        client: Async KurrentDB client.

    See ``DESIGN.md`` §7.1 for behaviour, §8 for the concurrency contract,
    §9 for resume semantics, §10 for rewind/compaction handling.
    """

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client
        self._revisions = RevisionTracker()

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        raise NotImplementedError

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        raise NotImplementedError

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        raise NotImplementedError

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        raise NotImplementedError

    async def append_event(self, session: Session, event: Event) -> Event:
        # Delegate to base first so temp-state trim + partial-event short-circuit
        # happen exactly as ADK expects (see base_session_service.py:114).
        await super().append_event(session, event)
        raise NotImplementedError
