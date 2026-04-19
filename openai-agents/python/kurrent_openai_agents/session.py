"""``KurrentDBSession`` — drop-in ``Session`` implementation for the OpenAI Agents SDK.

Implements the SDK's ``Session`` protocol (``src/agents/memory/session.py``)
by decomposing each session item into canonical events and writing them to
``AgentSession-{session_id}``. Items that don't map onto canonical conversation
events (reasoning, handoffs, MCP approvals, computer/shell calls, …) are
written as framework-specific ``OpenAIItem`` events carrying the raw dict.

Async — matches the SDK and ``AsyncKurrentDBClient``.

**v0 scope.** Core four methods (``get_items`` / ``add_items`` / ``pop_item``
/ ``clear_session``). Not yet implemented: memory, artifacts, eval managers,
``OpenAIResponsesCompactionSession``-style decorator support.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents.memory.session import SessionABC
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from ._codec import canonical_to_items, items_to_canonical
from ._schema import events as _events
from ._schema.events import OPENAI_EXTENSION_KEY
from ._schema.stream_names import for_session

if TYPE_CHECKING:  # pragma: no cover
    from agents.items import TResponseInputItem
    from agents.memory.session_settings import SessionSettings


logger = logging.getLogger("kurrent_openai_agents.session")

# KurrentDB metadata key for per-event token usage (SCHEMA.md §3.4).
USAGE_METADATA_KEY = "$usage"


class KurrentDBSession(SessionABC):
    """KurrentDB-backed ``Session`` implementation.

    One stream per session: ``AgentSession-{session_id}``. First write per
    session emits a ``SessionStarted`` lifecycle event carrying ``app_name``
    and ``user_id`` (framework-integration configuration — the OpenAI Agents
    SDK itself has no native app/user concept).

    Args:
        session_id: Opaque session identifier. Required.
        client: Async KurrentDB client.
        app_name: Application name — populates ``SessionStarted.app_name`` and
            is reserved for scoping future memory/artifact integration.
        user_id: End-user identifier — same treatment as ``app_name``.
        agent_name: Optional; surfaced on ``SessionStarted`` for observability.
        session_settings: SDK ``SessionSettings`` — stored on the instance as
            the protocol requires; otherwise unused in v0.
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

    # ----- Session protocol methods ------------------------------------------

    async def get_items(
        self, limit: int | None = None
    ) -> list[TResponseInputItem]:
        """Retrieve conversation history, newest-last. Optionally limit to latest N."""
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return []

        canonical_events: list = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue
            if isinstance(event, _events.SessionStarted | _events.SessionEnded):
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

        # Ensure SessionStarted is present — idempotent. The cheapest check is
        # to read one event back; if stream is missing, NO_STREAM append works.
        start_index = await self._count_items()

        canonical_events = items_to_canonical(
            [dict(item) for item in items],  # type: ignore[arg-type]
            start_index=start_index,
        )
        if not canonical_events:
            return

        # Write SessionStarted on first use. The append itself will fail on
        # NO_STREAM if the stream already exists — in that case, we re-enter
        # and skip it.
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
        """Remove and return the most recent item.

        **v0 limitation.** KurrentDB is append-only, so we implement this as
        "read last item, emit a ``SessionEnded``-style tombstone marker so
        that subsequent ``get_items`` calls ignore it". For now we return the
        item but do not actually mark it. A follow-up issue will add proper
        tombstoning via an ``OpenAIItem`` marker event. Callers that rely on
        ``pop_item`` (mostly the SDK's internal compaction flow) should avoid
        this session type until the tombstone is implemented.
        """
        items = await self.get_items()
        if not items:
            return None
        logger.warning(
            "pop_item() on KurrentDBSession is best-effort in v0 — the item is "
            "returned but remains in the stream. Tracking issue: pop_item v2."
        )
        return items[-1]

    async def clear_session(self) -> None:
        """Append a ``SessionEnded`` marker. The stream remains for audit."""
        ended = _events.SessionEnded(
            reason="cleared", timestamp=datetime.now(UTC)
        )
        await self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(ended)],
            current_version=StreamState.ANY,
        )

    # ----- internals ---------------------------------------------------------

    async def _count_items(self) -> int:
        """Count non-lifecycle, non-extension events currently in the stream."""
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return 0
        return sum(
            1
            for r in recorded
            if r.type not in {"SessionStarted", "SessionEnded"}
        )

    async def _emit_session_started_if_missing(self) -> None:
        started = _events.SessionStarted(
            app_name=self._app_name,
            user_id=self._user_id,
            agent_name=self._agent_name,
            timestamp=datetime.now(UTC),
        )
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

        v0: no automatic ``$usage`` mapping (the SDK aggregates usage at the
        run level, not per-item). Callers that want per-item usage should
        extend this method on a subclass or track usage externally.
        """
        del item, event
        return None
