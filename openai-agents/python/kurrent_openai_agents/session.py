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

from agents.lifecycle import RunHooksBase
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
from ._codec import canonical_to_items
from ._handoffs import ExpectedHandoff, _HandoffLedger
from ._stream_names import for_session

if TYPE_CHECKING:  # pragma: no cover
    from agents.items import TResponseInputItem
    from agents.memory.session_settings import SessionSettings


logger = logging.getLogger("kurrent_openai_agents.session")

# Lifecycle / out-of-conversation event types that get_items must skip.
_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset({
    "SessionStarted",
    "SessionEnded",
    "SessionContinuedAs",
})

_LIFECYCLE_PROTO_TYPES: tuple[type, ...] = (
    SessionStarted,
    SessionEnded,
    SessionContinuedAs,
)


class KurrentDBSession(SessionABC, RunHooksBase):
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
        self._ledger = _HandoffLedger(parent_stream=self._stream)

    # ----- Session protocol --------------------------------------------------

    async def get_items(
        self, limit: int | None = None
    ) -> list[TResponseInputItem]:
        """Retrieve conversation history, newest-last, re-flattening subagent
        transcripts inline at each ``SubagentStarted`` marker per spec §3."""
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return []

        items: list[TResponseInputItem] = []
        subsession_cache: dict[str, list[Any]] = {}

        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue

            if isinstance(event, SubagentStarted):
                # Emit the original handoff_call dict from extensions.openai.raw_item.
                handoff_call_items = canonical_to_items([event])
                items.extend(handoff_call_items)  # type: ignore[arg-type]

                # Inline the subsession transcript.
                subsession_stream = event.subsession_stream
                if subsession_stream:
                    sub_items = await self._read_subsession(subsession_stream, subsession_cache)
                    items.extend(sub_items)
                else:
                    logger.warning(
                        "SubagentStarted without subsession_stream on %s — skipping inline transcript",
                        self._stream,
                    )
                continue

            if isinstance(event, SubagentCompleted):
                handoff_output_items = canonical_to_items([event])
                items.extend(handoff_output_items)  # type: ignore[arg-type]
                continue

            if isinstance(event, _LIFECYCLE_PROTO_TYPES):
                continue

            items.extend(canonical_to_items([event]))  # type: ignore[arg-type]

        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Append items to the session stream(s), routing handoff-target items
        to ``AgentSubsession-{parent}-{agent_id}`` per SCHEMA_v2 §3.5."""
        if not items:
            return

        start_index = await self._count_items()

        from ._handoffs import route_items

        ops = route_items(
            [dict(item) for item in items],  # type: ignore[arg-type]
            ledger=self._ledger,
            session_id=self.session_id,
            start_index=start_index,
            timestamp=datetime.now(UTC),
        )
        if not ops:
            return

        if start_index == 0:
            await self._emit_session_started_if_missing()

        await self._write_ops(ops)

    async def _write_ops(self, ops: list[Any]) -> None:
        """Walk the route_items output in order, dispatching each op.

        ``SingleAppend`` → ``append_to_stream`` with int-typed metadata
        (existing serializer). ``DualAppend`` → atomic
        ``multi_append_to_stream`` with string-typed metadata
        (``serialize_for_multi_append``) so the v2 multi-append's
        string-only constraint is satisfied. Ordering is preserved exactly
        as route_items emitted, so subagent body items always land after
        their ``SubagentStarted`` lifecycle marker.
        """
        from kurrentdbclient import NewEvents

        from ._handoffs import DualAppend, SingleAppend

        for op in ops:
            if isinstance(op, SingleAppend):
                new_events = [_serialization.serialize(evt) for evt in op.events]
                await self._client.append_to_stream(
                    op.stream,
                    events=new_events,
                    current_version=StreamState.ANY,
                )
            elif isinstance(op, DualAppend):
                parent_stream, sub_stream = op.streams
                await self._client.multi_append_to_stream([
                    NewEvents(
                        stream_name=parent_stream,
                        events=[_serialization.serialize_for_multi_append(op.event)],
                        current_version=StreamState.ANY,
                    ),
                    NewEvents(
                        stream_name=sub_stream,
                        events=[_serialization.serialize_for_multi_append(op.event)],
                        current_version=StreamState.ANY,
                    ),
                ])
            else:
                logger.error("Unknown WriteOp type %r — dropping", type(op).__name__)

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

    # ----- RunHooksBase overrides -------------------------------------------

    async def on_handoff(
        self,
        context: Any,
        from_agent: Any,
        to_agent: Any,
    ) -> None:
        """Stash the expected handoff target until the next ``function_call`` arrives.

        Idempotent while ``expected`` is already pending — duplicate hook delivery
        from streaming retries or replay doesn't double-emit ``SubagentStarted``.
        """
        if self._ledger.expected is not None:
            new_target = getattr(to_agent, "name", "") or ""
            if new_target and new_target != self._ledger.expected.to_name:
                # Same expected slot held by a different prior handoff —
                # likely a nested handoff, which the schema's flat-only
                # stance does not support yet. Drop the second target;
                # log so the next agent debugging this knows where it went.
                logger.warning(
                    "on_handoff dropped nested target %r — prior expected %r still pending. "
                    "Nested handoffs are not yet canonicalised (schema flat-only stance).",
                    new_target, self._ledger.expected.to_name,
                )
            return
        self._ledger.expected = ExpectedHandoff(
            from_name=getattr(from_agent, "name", "") or "",
            to_name=getattr(to_agent, "name", "") or "",
            to_agent=to_agent,
        )

    async def on_agent_end(
        self,
        context: Any,
        agent: Any,
        output: Any,
    ) -> None:
        """Emit a deferred ``SubagentCompleted`` if this agent's subagent never returned.

        Some flows let the target agent produce a final output without handing
        back to the parent; without this catch we'd leak an open subagent.
        """
        agent_name = getattr(agent, "name", "") or ""
        for call_id, active in list(self._ledger.active.items()):
            if active.agent_type == agent_name and self._ledger.current_owner == active.subsession_stream:
                await self._emit_deferred_subagent_completed(call_id, output)
                return

    async def _emit_deferred_subagent_completed(
        self, call_id: str, output: Any
    ) -> None:
        """Emit a SubagentCompleted to BOTH streams when the target ended
        without producing a handoff_output (one-way handoff)."""
        from kurrentdbclient import NewEvents

        from ._codec import _map_handoff_output

        active = self._ledger.active.pop(call_id, None)
        if active is None:
            return
        synthetic_item = {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output if isinstance(output, str) else str(output or ""),
        }
        evt = _map_handoff_output(
            item=synthetic_item, agent_id=active.agent_id,
            message_index=-1, timestamp=datetime.now(UTC),
        )
        await self._client.multi_append_to_stream([
            NewEvents(
                stream_name=self._stream,
                events=[_serialization.serialize_for_multi_append(evt)],
                current_version=StreamState.ANY,
            ),
            NewEvents(
                stream_name=active.subsession_stream,
                events=[_serialization.serialize_for_multi_append(evt)],
                current_version=StreamState.ANY,
            ),
        ])
        self._ledger.current_owner = self._stream

    # ----- internals ---------------------------------------------------------

    async def _read_subsession(
        self,
        subsession_stream: str,
        cache: dict[str, list[Any]],
    ) -> list[TResponseInputItem]:
        """Read a subsession stream, skipping its mirrored Subagent* lifecycle
        and converting remaining events to OpenAI flat dicts."""
        if subsession_stream in cache:
            return cache[subsession_stream]  # type: ignore[return-value]

        try:
            recorded = await self._client.get_stream(subsession_stream)
        except NotFoundError:
            logger.warning(
                "Missing subsession stream %s referenced from %s — emitting handoff_call only",
                subsession_stream, self._stream,
            )
            cache[subsession_stream] = []
            return []

        canonical_events: list[Any] = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue
            # Skip the mirrored SubagentStarted/Completed copies — the parent
            # stream already accounts for those.
            if isinstance(event, (SubagentStarted, SubagentCompleted)):
                continue
            canonical_events.append(event)

        sub_items = canonical_to_items(canonical_events)
        cache[subsession_stream] = sub_items  # type: ignore[assignment]
        return sub_items  # type: ignore[return-value]

    async def _count_items(self) -> int:
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return 0
        skip = _LIFECYCLE_EVENT_TYPES | {"SubagentStarted", "SubagentCompleted"}
        return sum(1 for r in recorded if r.type not in skip)

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
