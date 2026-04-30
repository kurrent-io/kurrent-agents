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
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    SessionEnded,
    SessionStarted,
)
from kurrentdbclient import KurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError
from strands.session.session_manager import SessionManager
from strands.types.session import SessionAgent

from . import _serialization
from ._codec import (
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

# KurrentDB metadata key for per-event token usage (SCHEMA.md §3.4).
USAGE_METADATA_KEY = "$usage"

# Canonical event types eligible to carry ``$usage`` (written only on assistant
# events, per ``schema/SCHEMA_v2.md §3.6``).
_ASSISTANT_EVENT_CLASSES: tuple[type, ...] = (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    AssistantThinkingGenerated,
)


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
