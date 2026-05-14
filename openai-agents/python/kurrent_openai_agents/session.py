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
from ._handoffs import ExpectedHandoff, PendingHandoffCall, _HandoffLedger
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
        # Session-wide monotonic counter for message_index. None = not yet
        # initialised; lazily seeded from the union of parent + subsession
        # streams on the first add_items call.
        self._next_item_index: int | None = None

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
                # SubagentCompleted is a lifecycle-only marker — the synthetic
                # close triggered by on_agent_end has no underlying SDK item.
                # The subsession stream holds the ToolResultReceived for the
                # handoff_output, which is the SDK-visible "transfer" record.
                continue

            if isinstance(event, _LIFECYCLE_PROTO_TYPES):
                continue

            items.extend(canonical_to_items([event]))  # type: ignore[arg-type]

        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Append items to the session stream(s), routing handoff-target items
        to ``AgentSubsession-{parent}-{agent_id}`` per SCHEMA_v2 §3.5.

        Called even with an empty list so that pending_close entries stashed by
        on_agent_end are drained on the next SDK-driven invocation.
        """
        if not items and not self._ledger.pending_close:
            return

        await self._init_next_index_if_needed()
        start_index = self._next_item_index  # guaranteed non-None after init
        assert start_index is not None

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
        # Advance the session-wide counter by the number of items processed.
        # handoff_call/output items each consume one offset slot even though
        # they emit a lifecycle event without a message_index field.
        self._next_item_index = start_index + len(items)

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

    async def on_llm_end(
        self,
        context: Any,
        agent: Any,
        response: Any,
    ) -> None:
        """Capture every handoff function_call in the model response.

        The SDK fires this hook with the raw ``ModelResponse`` immediately
        after the LLM call returns, BEFORE the SDK resolves which handoff to
        invoke and BEFORE ``on_handoff`` fires. We walk ``response.output``
        and stash a ``PendingHandoffCall(tool_name, call_id)`` for each
        function call whose name matches one of the source agent's registered
        handoff tool names. ``on_handoff`` then pops the matching entry to
        build a precisely-scoped ``ExpectedHandoff``.

        The list is rebuilt per LLM response — unchosen candidates from a
        previous response are dropped here so they don't drift forward.
        """
        # Drop stale candidates from the previous LLM response.
        self._ledger.pending_calls.clear()

        tool_map = self._handoff_tool_map(agent)
        if not tool_map:
            return

        from_name = getattr(agent, "name", "") or ""
        outputs = getattr(response, "output", None) or []
        for output in outputs:
            name = (
                output.get("name") if isinstance(output, dict) else getattr(output, "name", None)
            )
            call_id = (
                output.get("call_id")
                if isinstance(output, dict)
                else getattr(output, "call_id", None)
            )
            if not name or not call_id:
                continue
            target_name_hint = tool_map.get(name)
            if target_name_hint is None:
                continue
            self._ledger.pending_calls.append(
                PendingHandoffCall(
                    from_name=from_name,
                    target_name_hint=target_name_hint,
                    tool_name=name,
                    call_id=call_id,
                )
            )

    async def on_handoff(
        self,
        context: Any,
        from_agent: Any,
        to_agent: Any,
    ) -> None:
        """Stash the expected handoff target until the matching ``function_call`` arrives.

        When ``on_llm_end`` populated a matching ``PendingHandoffCall``, we
        copy its exact ``(tool_name, call_id)`` into ``ExpectedHandoff`` so
        ``route_items`` can promote on a strict match. Without that entry
        (e.g. ``on_llm_end`` not wired in tests), fall back to resolving
        ``tool_name`` from ``from_agent.handoffs`` directly — name-only
        matching with the ``transfer_to_`` prefix as a final safety net.

        Idempotent while ``expected`` is already pending — duplicate hook
        delivery from streaming retries or replay doesn't double-emit.
        """
        from_name = getattr(from_agent, "name", "") or ""
        to_name = getattr(to_agent, "name", "") or ""

        if self._ledger.expected is not None:
            if to_name and to_name != self._ledger.expected.to_name:
                # Same expected slot held by a different prior handoff —
                # likely a nested handoff, which the schema's flat-only
                # stance does not support yet. Drop the second target;
                # log so the next agent debugging this knows where it went.
                logger.warning(
                    "on_handoff dropped nested target %r — prior expected %r still pending. "
                    "Nested handoffs are not yet canonicalised (schema flat-only stance).",
                    to_name, self._ledger.expected.to_name,
                )
            return

        match = self._ledger.pop_pending_call(from_name=from_name, to_name=to_name)
        if match is not None:
            tool_name: str | None = match.tool_name
            call_id: str | None = match.call_id
        else:
            tool_name = self._resolve_handoff_tool_name(from_agent, to_agent)
            call_id = None

        self._ledger.expected = ExpectedHandoff(
            from_name=from_name,
            to_name=to_name,
            to_agent=to_agent,
            tool_name=tool_name,
            call_id=call_id,
        )

    @staticmethod
    def _handoff_tool_map(agent: Any) -> dict[str, str]:
        """Map ``Handoff.tool_name`` → target agent name for ``agent.handoffs``.

        Mirrors the SDK's own handoff registration: ``Handoff`` instances
        expose ``tool_name`` directly; bare ``Agent`` entries get the
        default ``transfer_to_<slug>`` name via ``Handoff.default_tool_name``.
        Returns an empty dict when the agent registers no handoffs or when
        the agent type isn't recognisable.
        """
        result: dict[str, str] = {}
        handoffs = getattr(agent, "handoffs", None) or []
        for entry in handoffs:
            entry_agent_name = (
                getattr(entry, "agent_name", None) or getattr(entry, "name", None)
            )
            if not entry_agent_name:
                continue
            tool_name = getattr(entry, "tool_name", None)
            if tool_name:
                result[tool_name] = entry_agent_name
                continue
            # Bare Agent — synthesise the default tool name.
            try:
                from agents.handoffs import Handoff as _Handoff

                result[_Handoff.default_tool_name(entry)] = entry_agent_name
            except Exception:  # pragma: no cover — defensive fallback
                import re as _re

                slug = _re.sub(r"[^a-zA-Z0-9_]", "_", entry_agent_name).lower()
                result[f"transfer_to_{slug}"] = entry_agent_name
        return result

    @staticmethod
    def _resolve_handoff_tool_name(from_agent: Any, to_agent: Any) -> str | None:
        """Fallback resolution when on_llm_end didn't capture a pending call.

        Used only for callers that wire ``on_handoff`` without ``on_llm_end``
        (mainly tests). Production paths populate ``ExpectedHandoff`` from a
        ``PendingHandoffCall`` and never reach this branch.
        """
        tool_map = KurrentDBSession._handoff_tool_map(from_agent)
        to_name = getattr(to_agent, "name", "") or ""
        for tool_name, target in tool_map.items():
            if target == to_name:
                return tool_name
        return None

    async def on_agent_end(
        self,
        context: Any,
        agent: Any,
        output: Any,
    ) -> None:
        """Defer SubagentCompleted to the next add_items call.

        The SDK fires on_agent_end BEFORE the final save_result_to_session call,
        so writing SubagentCompleted here would race with the target's last batch
        of items. Instead, we stash (call_id → summary) in pending_close and let
        route_items drain it at the end of the next add_items invocation, after
        the final items have been flushed.
        """
        agent_name = getattr(agent, "name", "") or ""
        summary = output if isinstance(output, str) else str(output or "")
        for call_id, active in list(self._ledger.active.items()):
            if active.agent_type == agent_name:
                self._ledger.pending_close[call_id] = summary[:512]
                return

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

    async def _init_next_index_if_needed(self) -> None:
        """Lazily seed ``_next_item_index`` from the session-wide event count.

        Scans the parent stream for non-lifecycle events, then follows every
        ``SubagentStarted.subsession_stream`` reference to count non-lifecycle
        events there too. The sum is the correct session-wide monotonic offset
        for the next ``add_items`` batch.

        After the first call this is a no-op (counter is already set).
        """
        if self._next_item_index is not None:
            return

        skip = _LIFECYCLE_EVENT_TYPES | {"SubagentStarted", "SubagentCompleted"}
        count = 0
        subsession_streams: list[str] = []

        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            self._next_item_index = 0
            return

        for record in recorded:
            if record.type not in skip:
                count += 1
            elif record.type == "SubagentStarted":
                # Each SubagentStarted represents one consumed flat-list slot
                # (the parent's handoff_call item) that has no message_index
                # event of its own — count it here so the next add_items
                # batch resumes at the right offset.
                count += 1
                evt = _serialization.deserialize(record)
                if evt is not None and isinstance(evt, SubagentStarted) and evt.subsession_stream:
                    subsession_streams.append(evt.subsession_stream)
            # SubagentCompleted is synthetic (no underlying SDK item), so it
            # consumes no flat-list slot — intentionally not counted.

        for sub_stream in subsession_streams:
            try:
                sub_recorded = await self._client.get_stream(sub_stream)
            except NotFoundError:
                continue
            count += sum(1 for r in sub_recorded if r.type not in skip)

        self._next_item_index = count

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

