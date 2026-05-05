"""``KurrentDBSession`` — drop-in ``Session`` implementation for the OpenAI Agents SDK.

Implements the SDK's ``Session`` protocol (``src/agents/memory/session.py``)
by decomposing each session item into canonical events and writing them to
``AgentSession-{session_id}``. Items that don't map onto canonical
conversation events ride as framework-specific ``OpenAIItem`` events
carrying the raw dict. See ``DESIGN.md`` for the full mapping table.

Async — matches the SDK and ``AsyncKurrentDBClient``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents.memory.session import SessionABC
from kurrent_agent_schema import (
    USAGE_METADATA_KEY,
    SessionContinuedAs,
    SessionEnded,
    SessionStarted,
    SubagentCompleted,
    SubagentStarted,
)
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from ._codec import canonical_to_items, items_to_canonical
from ._stream_names import for_session

if TYPE_CHECKING:  # pragma: no cover
    from agents.items import TResponseInputItem
    from agents.memory.session_settings import SessionSettings


logger = logging.getLogger("kurrent_openai_agents.session")

# Lifecycle / out-of-conversation event types that get_items must skip.
# Subagent lifecycle is canonical in v2 (SCHEMA_v2 §3.5) but lives on the
# parent session stream — same skip treatment as session lifecycle.
_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset({
    "SessionStarted",
    "SessionEnded",
    "SessionContinuedAs",
    "SubagentStarted",
    "SubagentCompleted",
})

_LIFECYCLE_PROTO_TYPES: tuple[type, ...] = (
    SessionStarted,
    SessionEnded,
    SessionContinuedAs,
    SubagentStarted,
    SubagentCompleted,
)


class KurrentDBSession(SessionABC):
    """KurrentDB-backed ``Session`` implementation.

    One stream per session: ``AgentSession-{session_id}``. First write per
    session emits a ``SessionStarted`` lifecycle event carrying ``app_name``
    and ``user_id`` (constructor configuration — the OpenAI Agents SDK
    itself has no native app/user concept).
    """

    def __init__(
        self,
        session_id: str,
        *,
        client: AsyncKurrentDBClient,
        app_name: str | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
        session_settings: SessionSettings | None = None,
    ) -> None:
        self.session_id = session_id
        self.session_settings = session_settings
        self._client = client
        self._app_name = app_name
        self._user_id = user_id
        self._agent_name = agent_name
        self._stream = for_session(session_id)

    # ----- Session protocol --------------------------------------------------

    async def get_items(
        self, limit: int | None = None
    ) -> list[TResponseInputItem]:
        """Retrieve conversation history, newest-last."""
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return []

        canonical_events: list = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue
            if isinstance(event, _LIFECYCLE_PROTO_TYPES):
                continue
            canonical_events.append(event)

        items = canonical_to_items(canonical_events)
        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items  # type: ignore[return-value]

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Append items to the session stream, emitting canonical events."""
        if not items:
            return

        start_index = await self._count_items()

        canonical_events = items_to_canonical(
            [dict(item) for item in items],  # type: ignore[arg-type]
            start_index=start_index,
        )
        if not canonical_events:
            return

        if start_index == 0:
            await self._emit_session_started_if_missing()

        new_events = [
            _serialization.serialize(
                event,
                metadata=self._event_metadata_for(item, event),
            )
            for item, event in zip(items, canonical_events, strict=False)
        ]
        await self._client.append_to_stream(
            self._stream,
            events=new_events,
            current_version=StreamState.ANY,
        )

    async def pop_item(self) -> TResponseInputItem | None:
        """Best-effort pop — returns the last item but does not remove it.

        Append-only storage means proper "pop" needs a tombstone marker;
        deferred until a concrete caller needs it (DESIGN.md §8 Q1).
        """
        items = await self.get_items()
        if not items:
            return None
        logger.warning(
            "pop_item() on KurrentDBSession is best-effort — the item is "
            "returned but remains in the stream."
        )
        return items[-1]

    async def clear_session(self) -> None:
        """Append a ``SessionEnded`` marker. The stream remains for audit."""
        ended = SessionEnded(reason="cleared")
        ended.timestamp.FromDatetime(datetime.now(UTC).replace(tzinfo=None))
        await self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(ended)],
            current_version=StreamState.ANY,
        )

    # ----- internals ---------------------------------------------------------

    async def _count_items(self) -> int:
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return 0
        return sum(
            1 for r in recorded if r.type not in _LIFECYCLE_EVENT_TYPES
        )

    async def _emit_session_started_if_missing(self) -> None:
        started = SessionStarted()
        started.timestamp.FromDatetime(datetime.now(UTC).replace(tzinfo=None))
        if self._app_name:
            started.app_name = self._app_name
        if self._user_id:
            started.user_id = self._user_id
        if self._agent_name:
            started.agent_name = self._agent_name
        try:
            await self._client.append_to_stream(
                self._stream,
                events=[_serialization.serialize(started)],
                current_version=StreamState.NO_STREAM,
            )
        except Exception:
            # Stream exists — SessionStarted already written. Benign.
            pass

    def _event_metadata_for(
        self, item: Any, event: Any
    ) -> dict[str, Any] | None:
        """Build the KurrentDB event metadata dict for an OpenAI item.

        v0: no automatic ``$usage`` mapping. The SDK aggregates usage at the
        run level, not per-item. ``USAGE_METADATA_KEY`` is imported here for
        subclassers who want to attach per-item usage from out-of-band data.
        """
        del item, event
        _ = USAGE_METADATA_KEY  # imported for subclassers
        return None
