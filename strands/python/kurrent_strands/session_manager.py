"""``KurrentDBSessionManager`` — see ``DESIGN.md`` §3.

Implements ``strands.session.SessionManager`` against KurrentDB. Writes
canonical events per ``SCHEMA.md``; preserves Strands-specific state
(``SessionAgent`` shape, non-canonical content blocks, message metadata) in
``extensions.strands`` and in the framework-specific ``StrandsAgentState``
event.

Sync — Strands hook callbacks are synchronous, so this uses the sync
``KurrentDBClient``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from kurrent_agent_schema import (
    USAGE_METADATA_KEY,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    InterruptIssued,
    InterruptResolved,
    SessionEnded,
    SessionStarted,
)
from kurrentdbclient import KurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError
from strands.hooks import BeforeToolCallEvent, HookRegistry
from strands.session.session_manager import SessionManager
from strands.types.session import SessionAgent

from . import _serialization
from ._codec import (
    _set_strands_extension,
    canonical_to_messages,
    extract_usage_metadata,
    message_to_canonical,
)
from ._strands_events import MessageRedacted, StrandsAgentState
from ._stream_names import for_session

if TYPE_CHECKING:  # pragma: no cover
    from strands.agent.agent import Agent
    from strands.types.content import Message

logger = logging.getLogger("kurrent_strands.session_manager")

# Canonical event types eligible to carry ``$usage`` (written only on assistant
# events, per ``schema/SCHEMA_v2.md §3.6``).
_ASSISTANT_EVENT_CLASSES: tuple[type, ...] = (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    AssistantThinkingGenerated,
)


def _interpret_outcome(response: Any) -> str:
    """Map an opaque Strands interrupt response to a canonical outcome string.

    ``SCHEMA_v2.md §3.3`` defines the canonical set:
    ``allow`` / ``allow_once`` / ``allow_always`` / ``deny`` / ``cancel`` /
    ``answered`` / ``timeout``. Strands treats interrupt responses as
    opaque (free-form caller data), so we map the most common conventions
    (booleans, ``"allow"``/``"deny"`` strings, ``{"approve": …}`` /
    ``{"decision": …}`` dicts) and fall back to ``answered`` for anything
    else (the canonical "input" interrupt-kind catch-all).
    """
    if response is None:
        return "timeout"
    if isinstance(response, bool):
        return "allow" if response else "deny"
    if isinstance(response, str):
        normalized = response.strip().lower()
        if normalized in {"allow", "allow_once", "allow_always", "approve", "approved", "yes"}:
            return "allow"
        if normalized in {"deny", "denied", "reject", "rejected", "no"}:
            return "deny"
        if normalized in {"cancel", "cancelled", "canceled"}:
            return "cancel"
    if isinstance(response, dict):
        if response.get("approve") is True or response.get("decision") in {"allow", "approve"}:
            return "allow"
        if response.get("approve") is False or response.get("decision") in {"deny", "reject"}:
            return "deny"
        if response.get("decision") == "cancel":
            return "cancel"
    return "answered"


class KurrentDBSessionManager(SessionManager):
    """KurrentDB-backed ``SessionManager`` for Strands agents.

    One stream per session: ``AgentSession-{session_id}``. The stream carries:

    - A ``SessionStarted`` lifecycle marker at position 0 (emitted on first
      construction for a new session id).
    - Canonical conversation events decomposed from each ``Message`` that
      Strands appends to the agent via the ``MessageAddedEvent`` hook.
    - ``StrandsAgentState`` snapshots emitted on every ``sync_agent`` call
      (after every message and after each invocation).
    - ``MessageRedacted`` events when a guardrail redacts a prior message.

    Args:
        client: Sync KurrentDB client.
        session_id: Opaque session identifier (passed to the stream name).
        app_name: Application name — populates ``SessionStarted.app_name`` and
            is reserved for scoping future memory/artifact integration.
            Strands itself has no native ``app_name`` concept; treat it as
            framework-integration configuration.
        user_id: End-user identifier — same treatment as ``app_name``.
        agent_name: Optional; surfaced on ``SessionStarted`` for observability.
    """

    def __init__(
        self,
        *,
        client: KurrentDBClient,
        session_id: str,
        app_name: str | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._app_name = app_name
        self._user_id = user_id
        self._agent_name = agent_name
        self._stream = for_session(session_id)
        # Monotonic message index assigned to each appended message. Populated
        # when ``initialize`` restores a session, or reset to 0 on create.
        self._next_message_index = 0
        # Dedupe trackers for the interrupt observer hook (DEV-1661). Keyed
        # by the canonical ``request_id`` (= Strands ``toolUseId``), populated
        # from the stream on ``initialize`` so resumed sessions don't re-emit.
        self._issued_request_ids: set[str] = set()
        self._resolved_request_ids: set[str] = set()

    # ----- HookProvider ------------------------------------------------------

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the base ``SessionManager`` callbacks plus our interrupt
        observer (``BeforeToolCallEvent`` → :meth:`_on_before_tool_call`)."""
        super().register_hooks(registry, **kwargs)
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)

    # ----- SessionManager abstract methods -----------------------------------

    def initialize(self, agent: Agent, **kwargs: Any) -> None:
        """Restore ``agent.messages`` + conversation-manager state from the stream.

        On first run (stream doesn't exist), emit a ``SessionStarted`` marker.
        """
        try:
            recorded = self._client.get_stream(self._stream)
        except NotFoundError:
            self._emit_session_started()
            self._next_message_index = 0
            return

        canonical_events: list = []
        latest_agent_state: StrandsAgentState | None = None

        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue
            if isinstance(event, SessionStarted | SessionEnded):
                continue
            if isinstance(event, StrandsAgentState):
                latest_agent_state = event
                continue
            if isinstance(event, InterruptIssued):
                # Pre-populate the observer's dedupe set so a resumed session
                # doesn't re-emit an interrupt that was already recorded.
                self._issued_request_ids.add(event.request_id)
                continue
            if isinstance(event, InterruptResolved):
                self._resolved_request_ids.add(event.request_id)
                continue
            # MessageRedacted handling is a v1 follow-up; for now keep the
            # events (they'll show as unknown content to canonical_to_messages).
            canonical_events.append(event)

        messages = canonical_to_messages(canonical_events)
        agent.messages = messages  # type: ignore[attr-defined]

        # Restore SessionAgent state (conversation_manager + user state +
        # internal state). If missing, leave the agent's defaults in place.
        if latest_agent_state is not None:
            session_agent = SessionAgent(
                agent_id=latest_agent_state.agent_id,
                state=dict(latest_agent_state.state),
                conversation_manager_state=dict(
                    latest_agent_state.conversation_manager_state
                ),
                _internal_state=dict(latest_agent_state.internal_state),
            )
            session_agent.initialize_internal_state(agent)
            if session_agent.conversation_manager_state:
                agent.conversation_manager.restore_from_session(
                    session_agent.conversation_manager_state
                )
            if session_agent.state:
                agent.state.update(session_agent.state)

        # Monotonic message index continues from where we left off.
        self._next_message_index = len(messages)

    def append_message(self, message: Message, agent: Agent, **kwargs: Any) -> None:
        """Persist a new ``Message`` as canonical events with ``$usage`` metadata."""
        index = self._next_message_index
        self._next_message_index += 1
        canonical_events = message_to_canonical(message, message_index=index)
        if not canonical_events:
            return
        usage_metadata = extract_usage_metadata(message)
        new_events = [
            _serialization.serialize(
                canonical,
                metadata=(
                    {USAGE_METADATA_KEY: usage_metadata}
                    if usage_metadata is not None
                    and isinstance(canonical, _ASSISTANT_EVENT_CLASSES)
                    else None
                ),
            )
            for canonical in canonical_events
        ]
        self._client.append_to_stream(
            self._stream,
            events=new_events,
            current_version=StreamState.ANY,
        )

    def sync_agent(self, agent: Agent, **kwargs: Any) -> None:
        """Snapshot the agent's ``SessionAgent`` shape as a ``StrandsAgentState`` event."""
        try:
            session_agent = SessionAgent.from_agent(agent)
        except ValueError:
            # Agent has no agent_id set — nothing durable to snapshot.
            logger.debug("Skipping sync_agent: agent.agent_id is not set")
            return
        event = StrandsAgentState(
            agent_id=session_agent.agent_id,
            state=dict(session_agent.state or {}),
            conversation_manager_state=dict(session_agent.conversation_manager_state or {}),
            internal_state=dict(session_agent._internal_state or {}),
            timestamp=datetime.now(UTC),
        )
        self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(event)],
            current_version=StreamState.ANY,
        )

    # ----- interrupt emission (DEV-1661) -------------------------------------

    def emit_interrupt_issued(
        self,
        *,
        tool_use: dict[str, Any],
        kind: str = "approval",
        prompt: str | None = None,
    ) -> None:
        """Emit a canonical ``InterruptIssued`` event for a tool approval.

        Strands' ``Interrupt`` is post-hoc per ``SCHEMA_v2.md §3.3``:
        ``request_id`` is set to ``tool_use["toolUseId"]``, the model-assigned
        id that also appears as ``call_id`` on the eventual
        ``AssistantToolCallsGenerated`` (when allowed). The proposed call is
        placed under ``extensions.strands.interrupt.proposed_call`` per the
        §3.3 soft convention so cross-framework readers (e.g. Capacitor's
        approval-prompt UI) render uniformly.

        Args:
            tool_use: The Strands ``ToolUse`` dict — must carry ``toolUseId``,
                ``name``, and optionally ``input``.
            kind: Interrupt kind. Defaults to ``"approval"`` — the only kind
                Strands surfaces today.
            prompt: Optional human-readable prompt (e.g. "Approve calling X?").
        """
        tool_use_id = tool_use.get("toolUseId") or ""
        tool_name = tool_use.get("name") or ""
        arguments = dict(tool_use.get("input") or {})

        evt = InterruptIssued(
            request_id=tool_use_id,
            kind=kind,
            tool_name=tool_name,
            timestamp=datetime.now(UTC),
        )
        if prompt is not None:
            evt.prompt = prompt

        _set_strands_extension(
            evt,
            {
                "interrupt": {
                    "proposed_call": {
                        "id": tool_use_id,
                        "name": tool_name,
                        "arguments": arguments,
                    }
                }
            },
        )

        self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(evt)],
            current_version=StreamState.ANY,
        )

    def emit_interrupt_resolved(
        self,
        *,
        tool_use_id: str,
        outcome: str,
        response: Any = None,
    ) -> None:
        """Emit a canonical ``InterruptResolved`` event.

        ``outcome`` is one of the ``SCHEMA_v2 §3.3`` values: ``allow`` /
        ``allow_once`` / ``allow_always`` / ``deny`` / ``cancel`` /
        ``answered`` / ``timeout``. The original Strands response (free-form,
        opaque to the framework) rides under
        ``extensions.strands.interrupt.resolution`` for same-framework replay.

        Args:
            tool_use_id: The model-assigned ``toolUseId`` matching the
                originating ``InterruptIssued.request_id``.
            outcome: Canonical outcome string.
            response: Optional original user-supplied response payload.
        """
        evt = InterruptResolved(
            request_id=tool_use_id,
            outcome=outcome,
            timestamp=datetime.now(UTC),
        )
        if response is not None:
            _set_strands_extension(evt, {"interrupt": {"resolution": response}})

        self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(evt)],
            current_version=StreamState.ANY,
        )

    def redact_latest_message(
        self,
        redact_message: Message,
        agent: Agent,
        **kwargs: Any,
    ) -> None:
        """Emit a ``MessageRedacted`` marker for the most recently appended message."""
        # The message being redacted is the last one appended — so its index
        # is ``_next_message_index - 1`` (the counter advances after append).
        redacted_index = max(0, self._next_message_index - 1)
        event = MessageRedacted(
            message_index=redacted_index,
            redact_message=dict(redact_message),
            timestamp=datetime.now(UTC),
        )
        self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(event)],
            current_version=StreamState.ANY,
        )

    # ----- internals ---------------------------------------------------------

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Observer hook — auto-emit canonical interrupt events.

        Runs on every ``BeforeToolCallEvent``. Inspects ``agent._interrupt_state``
        for an interrupt whose Strands-generated id embeds the current
        ``tool_use["toolUseId"]`` (the framework's id format is
        ``f"v1:before_tool_call:{toolUseId}:{uuid5(...)}"``), and:

        - emits a canonical ``InterruptIssued`` the first time we see the
          interrupt for this ``toolUseId`` (a sibling user hook raised it
          via ``event.interrupt(...)`` in this same dispatch);
        - emits a canonical ``InterruptResolved`` once
          ``Interrupt.response`` is populated (after the caller resumed the
          agent with an interruptResponse).

        Dedupe is handled by ``self._issued_request_ids`` /
        ``self._resolved_request_ids``, both pre-populated from the stream
        in :meth:`initialize` so resumed sessions don't re-emit.
        """
        interrupt_state = getattr(event.agent, "_interrupt_state", None)
        if interrupt_state is None:
            return
        interrupts = getattr(interrupt_state, "interrupts", None) or {}
        if not interrupts:
            return

        tool_use = event.tool_use
        tool_use_id = tool_use.get("toolUseId") if isinstance(tool_use, dict) else None
        if not tool_use_id:
            return

        # Strands embeds the toolUseId inside its generated interrupt id.
        matching = next(
            (i for i in interrupts.values() if tool_use_id in getattr(i, "id", "")),
            None,
        )
        if matching is None:
            return

        if tool_use_id not in self._issued_request_ids:
            prompt = (
                str(matching.reason)
                if getattr(matching, "reason", None) is not None
                else None
            )
            self.emit_interrupt_issued(tool_use=tool_use, prompt=prompt)
            self._issued_request_ids.add(tool_use_id)

        response = getattr(matching, "response", None)
        if response is not None and tool_use_id not in self._resolved_request_ids:
            self.emit_interrupt_resolved(
                tool_use_id=tool_use_id,
                outcome=_interpret_outcome(response),
                response=response,
            )
            self._resolved_request_ids.add(tool_use_id)

    def _emit_session_started(self) -> None:
        started = SessionStarted(
            app_name=self._app_name,
            user_id=self._user_id,
            agent_name=self._agent_name,
            timestamp=datetime.now(UTC),
        )
        self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(started)],
            current_version=StreamState.NO_STREAM,
        )
