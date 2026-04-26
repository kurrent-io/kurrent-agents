"""``KurrentDBSessionService`` — see ``DESIGN.md`` §7.1.

Implements ``google.adk.sessions.BaseSessionService`` backed by KurrentDB.
Events are decomposed into canonical form on write (see ``_codec.py``) and
reconstructed on read. ``SessionStarted`` / ``SessionEnded`` lifecycle markers
are emitted by this service directly; conversation and state events go through
the codec.

**v1 scope.** Single session stream per ``session_id``; state deltas ride in
``extensions.adk.actions.state_delta`` within each canonical event. App-scoped
and user-scoped state routing (to separate ``AgentAppState`` / ``AgentUserState``
streams) is a follow-up.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from google.adk.events.event import Event as AdkEvent
from google.adk.sessions.base_session_service import (
    BaseSessionService,
    GetSessionConfig,
    ListSessionsResponse,
)
from google.adk.sessions.session import Session
from google.genai import types
from kurrent_agent_schema.usage import USAGE_METADATA_KEY
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError, WrongCurrentVersionError

from . import _serialization
from . import events as _events
from ._codec import canonical_to_events, event_to_canonical, extract_usage_metadata
from ._revisions import RevisionTracker, SessionKey, StaleSessionError
from ._streams import for_session
from .events import ADK_EXTENSION_KEY

logger = logging.getLogger("kurrent_google_adk.session_service")

# Canonical event types eligible for $usage metadata.
_ASSISTANT_EVENT_CLASSES: tuple[type, ...] = (
    _events.AssistantTextGenerated,
    _events.AssistantToolCallsGenerated,
)


class KurrentDBSessionService(BaseSessionService):
    """KurrentDB-backed session service."""

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client
        self._revisions = RevisionTracker()

    # ------------------------------------------------------------------ create

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        session_id = session_id or str(uuid.uuid4())
        started = _events.SessionStarted(
            app_name=app_name,
            user_id=user_id,
            timestamp=datetime.now(UTC),
        )
        stream = for_session(session_id)
        new_revision = await self._client.append_to_stream(
            stream,
            events=_serialization.serialize(started),
            current_version=StreamState.NO_STREAM,
        )
        key = SessionKey(app_name, user_id, session_id)
        self._revisions.record_session(key, new_revision)

        return Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=dict(state or {}),
            events=[],
            last_update_time=started.timestamp.timestamp(),
        )

    # --------------------------------------------------------------------- get

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        stream = for_session(session_id)
        try:
            recorded_events = await self._client.get_stream(stream)
        except NotFoundError:
            return None

        canonical_events: list = []
        # Map: source ADK event id (from extensions.adk.id) → $usage payload.
        # The codec's v1 reconstruction path doesn't preserve LlmResponse
        # metadata, so we re-hydrate usage_metadata from the KurrentDB event
        # metadata channel after reconstruction. See DEV-1479.
        usage_by_event_id: dict[str, dict] = {}
        last_revision = -1
        last_timestamp = 0.0
        ended = False

        for recorded in recorded_events:
            last_revision = recorded.stream_position
            canonical = _serialization.deserialize(recorded)
            if canonical is None:
                continue
            if isinstance(canonical, _events.SessionStarted):
                last_timestamp = canonical.timestamp.timestamp()
                continue
            if isinstance(canonical, _events.SessionEnded):
                ended = True
                last_timestamp = canonical.timestamp.timestamp()
                continue

            _collect_usage(recorded, canonical, usage_by_event_id)

            canonical_events.append(canonical)
            if hasattr(canonical, "timestamp"):
                last_timestamp = max(last_timestamp, canonical.timestamp.timestamp())

        # Record revision even if we saw only lifecycle events — writes need it.
        key = SessionKey(app_name, user_id, session_id)
        if last_revision >= 0:
            self._revisions.record_session(key, last_revision)

        adk_events = canonical_to_events(canonical_events)
        _apply_usage_metadata(adk_events, usage_by_event_id)

        # Apply GetSessionConfig filters to adk_events only; state is always full.
        if config is not None:
            if config.after_timestamp is not None:
                adk_events = [
                    e for e in adk_events if e.timestamp >= config.after_timestamp
                ]
            if config.num_recent_events is not None:
                if config.num_recent_events == 0:
                    adk_events = []
                elif config.num_recent_events > 0:
                    adk_events = adk_events[-config.num_recent_events :]

        state = _fold_session_state(canonical_events)

        session = Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=state,
            events=adk_events,
            last_update_time=last_timestamp,
        )
        if ended:
            logger.debug("Session %s has a SessionEnded marker", session_id)
        return session

    # ------------------------------------------------------------------- list

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        # v1 placeholder: $ce-AgentSession enumeration depends on the category
        # projection being enabled on the KurrentDB server and requires filtering
        # by (app_name, user_id) pulled from each stream's SessionStarted event.
        # Deferred until we have concrete list_sessions use cases; the ADK UI
        # doesn't exercise this path for the core flow.
        raise NotImplementedError(
            "list_sessions is not implemented yet; see DESIGN.md §13 open question 5"
        )

    # ---------------------------------------------------------------- delete

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        """Soft delete: append a ``SessionEnded`` event.

        The stream is preserved for audit. A future hard-delete path can
        tombstone the stream if a caller asks for it.
        """
        key = SessionKey(app_name, user_id, session_id)
        stream = for_session(session_id)
        ended = _events.SessionEnded(reason="deleted", timestamp=datetime.now(UTC))
        # Use StreamState.ANY to tolerate out-of-sync revision — delete is
        # idempotent and we don't want to race-loop on it.
        new_revision = await self._client.append_to_stream(
            stream,
            events=_serialization.serialize(ended),
            current_version=StreamState.ANY,
        )
        self._revisions.record_session(key, new_revision)

    # --------------------------------------------------------------- append

    async def append_event(self, session: Session, event: AdkEvent) -> AdkEvent:
        # Base class handles Event.partial short-circuit, temp-state apply/trim,
        # and in-memory session state/event updates.
        await super().append_event(session=session, event=event)

        if event.partial:
            # Base class returned early; we should not persist partials.
            return event

        key = SessionKey(session.app_name, session.user_id, session.id)
        canonical_events = event_to_canonical(event)
        if not canonical_events:
            return event

        usage_metadata = extract_usage_metadata(event)
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

        stream = for_session(session.id)
        await self._append_with_retry(key, stream, new_events)
        session.last_update_time = event.timestamp
        return event

    # ---------------------------------------------------- concurrency helpers

    async def _append_with_retry(
        self, key: SessionKey, stream: str, events: list
    ) -> int:
        """Append with optimistic concurrency; one retry after catch-up.

        See ``DESIGN.md`` §8.
        """
        expected = self._expected_revision(key)
        try:
            new_revision = await self._client.append_to_stream(
                stream, events=events, current_version=expected
            )
        except WrongCurrentVersionError:
            logger.info(
                "Concurrent writer detected on %s; re-reading and retrying once.",
                stream,
            )
            new_expected = await self._refresh_revision(stream)
            self._revisions.record_session(key, new_expected if new_expected is not None else -1)
            try:
                new_revision = await self._client.append_to_stream(
                    stream,
                    events=events,
                    current_version=new_expected
                    if new_expected is not None
                    else StreamState.NO_STREAM,
                )
            except WrongCurrentVersionError as err:
                raise StaleSessionError(
                    f"Stale session on stream {stream!r}; a concurrent writer modified it "
                    "during retry. Re-read via get_session and retry at the caller."
                ) from err
        self._revisions.record_session(key, new_revision)
        return new_revision

    def _expected_revision(self, key: SessionKey) -> int | StreamState:
        last_known = self._revisions.get(key).session
        return last_known if last_known is not None else StreamState.NO_STREAM

    async def _refresh_revision(self, stream: str) -> int | None:
        """Read the tip of a stream; return the last revision or ``None`` if empty."""
        try:
            response = await self._client.get_stream(stream, backwards=True, limit=1)
        except NotFoundError:
            return None
        events = list(response)
        if not events:
            return None
        return events[0].stream_position


# -----------------------------------------------------------------------------


def _collect_usage(
    recorded,
    canonical,
    usage_by_event_id: dict[str, dict],
) -> None:
    """Read ``$usage`` from a RecordedEvent and index it by source ADK event id.

    Only applies to assistant canonical events (which are the only ones the
    session service writes ``$usage`` onto, per SCHEMA.md §3.4).
    """
    if not isinstance(canonical, _ASSISTANT_EVENT_CLASSES):
        return
    metadata = _serialization.read_metadata(recorded)
    if not metadata:
        return
    usage = metadata.get(USAGE_METADATA_KEY)
    if not usage:
        return
    if canonical.extensions is None:
        return
    source_id = canonical.extensions.get(ADK_EXTENSION_KEY, {}).get("id")
    if source_id:
        usage_by_event_id[source_id] = usage


def _apply_usage_metadata(
    adk_events: list[AdkEvent], usage_by_event_id: dict[str, dict]
) -> None:
    """Re-hydrate ``Event.usage_metadata`` from the ``$usage`` we captured on write.

    The canonical ``$usage`` dict uses ``input_tokens`` / ``output_tokens`` /
    ``total_tokens`` / ``cached_input_tokens`` / ``reasoning_tokens``; ADK's
    ``GenerateContentResponseUsageMetadata`` uses the Gemini-native field names.
    """
    if not usage_by_event_id:
        return
    for event in adk_events:
        usage = usage_by_event_id.get(event.id)
        if usage is None:
            continue
        event.usage_metadata = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage.get("input_tokens"),
            candidates_token_count=usage.get("output_tokens"),
            total_token_count=usage.get("total_tokens"),
            cached_content_token_count=usage.get("cached_input_tokens"),
            thoughts_token_count=usage.get("reasoning_tokens"),
        )


def _fold_session_state(canonical_events: list) -> dict[str, Any]:
    """Rebuild ``Session.state`` from canonical events' extensions envelope.

    ADK state deltas live under ``extensions.adk.actions.state_delta`` on each
    emitted canonical event (the codec preserves them verbatim). Folding is
    left-to-right, most-recent-write-wins, honouring rewind boundaries.
    """
    state: dict[str, Any] = {}
    # Find the last rewind event, if any; everything before its
    # ``rewind_before_invocation_id`` is discarded.
    rewind_idx = None
    rewind_before: str | None = None
    for idx, event in enumerate(canonical_events):
        if isinstance(event, _events.Rewind):
            rewind_idx = idx
            rewind_before = event.rewind_before_invocation_id

    # Fold forward, skipping events rewound by the last Rewind.
    for idx, event in enumerate(canonical_events):
        if rewind_idx is not None and idx < rewind_idx:
            ext = _adk_extensions(event)
            this_inv = ext.get("invocation_id")
            if this_inv == rewind_before:
                # The first event of the rewound invocation and everything after
                # is discarded; break here rather than continue iterating.
                break
        delta = _state_delta_from(event)
        if delta:
            state.update(delta)
    return state


def _adk_extensions(event) -> dict[str, Any]:
    if getattr(event, "extensions", None) is None:
        return {}
    return event.extensions.get("adk", {})


def _state_delta_from(event) -> dict[str, Any]:
    if isinstance(event, _events.Rewind):
        return dict(event.state_delta or {})
    if isinstance(event, _events.StateDelta):
        return dict(event.delta or {})
    actions_ext = _adk_extensions(event).get("actions", {})
    return dict(actions_ext.get("state_delta") or {})
