"""KurrentDB-backed workflow checkpointing and multi-agent coordination.

Two independent pieces mirroring the MAF .NET ``Workflows/`` namespace:

* :class:`KurrentDBCheckpointStorage` — implements the MAF Python
  :class:`CheckpointStorage` protocol. Each checkpoint becomes a
  ``WorkflowCheckpoint`` event in a ``WorkflowCheckpoint-{workflow_name}``
  stream. The .NET mirror keys on ``session_id`` (its :class:`ICheckpointStore`
  exposes ``CreateCheckpointAsync(sessionId, …)``); the Python protocol filters
  by ``workflow_name`` instead, so the stream suffix follows the Python
  semantic. Checkpoint payloads are encoded via the upstream
  ``encode_checkpoint_value`` helper (pickle+base64 inside JSON) so the full
  ``WorkflowCheckpoint`` fidelity round-trips — including complex Python state
  that isn't natively JSON.

* :class:`KurrentDBGroupChatRecorder` — observes a running workflow's
  :class:`WorkflowEvent` stream and records canonical ``AgentTurnTaken`` /
  ``GroupChatCompleted`` events in a ``GroupChat-{chat_id}`` stream. Python's
  multi-agent model is executor-based (no ``GroupChatManager`` subclass to
  override, as in .NET), so the integration point is the event stream rather
  than strategy-function hooks. This shape works for any orchestration pattern
  (``GroupChatBuilder``, ``SequentialBuilder``, ``ConcurrentBuilder``,
  ``HandoffBuilder``, ``MagenticBuilder``, or a hand-built ``WorkflowBuilder``
  graph).

Neither event type is part of canonical schema v2 — they live in
MAF-specific streams alongside ``AgentSession-*``, and the cross-SDK reader
contract is that other frameworks simply ignore them. See
``schema/SCHEMA_v2.md §2`` (stream taxonomy).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_framework import WorkflowCheckpoint, WorkflowEvent

# ``_checkpoint_encoding`` is under a private module path (leading underscore),
# but it is the same helper the upstream ``FileCheckpointStorage`` relies on —
# there's no public re-export. Pinning ``agent-framework-core >= 1.1.0`` in
# pyproject.toml keeps this aligned with the version we've validated against;
# if upstream moves/renames the module this import will fail fast at startup.
from agent_framework._workflows._checkpoint_encoding import (
    decode_checkpoint_value,
    encode_checkpoint_value,
)
from agent_framework.exceptions import WorkflowCheckpointException
from kurrentdbclient import AsyncKurrentDBClient, NewEvent, StreamState
from kurrentdbclient.exceptions import NotFoundError

logger = logging.getLogger("kurrent_agent_framework.workflows")


WORKFLOW_CHECKPOINT_STREAM_PREFIX: str = "WorkflowCheckpoint-"
GROUP_CHAT_STREAM_PREFIX: str = "GroupChat-"

WORKFLOW_CHECKPOINT_EVENT_TYPE: str = "WorkflowCheckpoint"
AGENT_TURN_TAKEN_EVENT_TYPE: str = "AgentTurnTaken"
GROUP_CHAT_COMPLETED_EVENT_TYPE: str = "GroupChatCompleted"

_CHECKPOINT_ID_META_KEY: str = "$checkpointId"
_WORKFLOW_NAME_META_KEY: str = "$workflowName"
_PREVIOUS_CHECKPOINT_META_KEY: str = "$previousCheckpointId"


def workflow_checkpoint_stream(workflow_name: str) -> str:
    """Stream name for workflow checkpoints scoped to a workflow definition.

    Rejects empty / whitespace-only names so independent workflows can't
    silently collapse into a shared ``WorkflowCheckpoint-`` stream — the same
    guard :class:`KurrentDBAgentMemory` applies to app/user identifiers.
    """
    if workflow_name is None or not workflow_name.strip():
        raise ValueError("workflow_name must be a non-empty, non-whitespace string")
    return f"{WORKFLOW_CHECKPOINT_STREAM_PREFIX}{workflow_name}"


def group_chat_stream(chat_id: str) -> str:
    """Stream name for a multi-agent group chat's turn history.

    Rejects empty / whitespace-only ids — see :func:`workflow_checkpoint_stream`.
    """
    if chat_id is None or not chat_id.strip():
        raise ValueError("chat_id must be a non-empty, non-whitespace string")
    return f"{GROUP_CHAT_STREAM_PREFIX}{chat_id}"


# --- Checkpoint storage -----------------------------------------------------


class KurrentDBCheckpointStorage:
    """KurrentDB-backed :class:`CheckpointStorage` for MAF workflows.

    Each checkpoint is appended as a ``WorkflowCheckpoint`` event to a
    ``WorkflowCheckpoint-{workflow_name}`` stream. Event metadata carries the
    checkpoint id, workflow name, and previous-checkpoint id so reads can
    filter without deserialising the payload.

    Args:
        client: Async KurrentDB client.
        allowed_checkpoint_types: Optional allow-list of additional types
            (``"module:qualname"`` strings) permitted during checkpoint
            deserialisation. Forwarded to
            :func:`decode_checkpoint_value` alongside the built-in safe set
            (primitives, ``datetime``, ``uuid``, ``agent_framework`` internal
            types, ``openai.types``). See the upstream
            :class:`FileCheckpointStorage` for the same parameter.
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        allowed_checkpoint_types: Iterable[str] | None = None,
    ) -> None:
        self._client = client
        self._allowed_types: frozenset[str] = frozenset(allowed_checkpoint_types or ())

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        """Append a checkpoint to its workflow's stream, return the checkpoint id."""
        encoded = encode_checkpoint_value(checkpoint.to_dict())
        data = json.dumps(encoded, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        metadata: dict[str, Any] = {
            _CHECKPOINT_ID_META_KEY: checkpoint.checkpoint_id,
            _WORKFLOW_NAME_META_KEY: checkpoint.workflow_name,
        }
        if checkpoint.previous_checkpoint_id is not None:
            metadata[_PREVIOUS_CHECKPOINT_META_KEY] = checkpoint.previous_checkpoint_id
        metadata_bytes = json.dumps(metadata, separators=(",", ":")).encode("utf-8")

        await self._client.append_to_stream(
            stream_name=workflow_checkpoint_stream(checkpoint.workflow_name),
            current_version=StreamState.ANY,
            events=[
                NewEvent(
                    id=uuid.uuid4(),
                    type=WORKFLOW_CHECKPOINT_EVENT_TYPE,
                    data=data,
                    metadata=metadata_bytes,
                )
            ],
        )
        logger.debug(
            "Saved checkpoint %s to stream %s",
            checkpoint.checkpoint_id,
            workflow_checkpoint_stream(checkpoint.workflow_name),
        )
        return checkpoint.checkpoint_id

    async def load(
        self,
        checkpoint_id: str,
        *,
        resolve_timeout: float = 2.0,
    ) -> WorkflowCheckpoint:
        """Load a checkpoint by id.

        Scans every ``WorkflowCheckpoint-*`` stream via the ``$ce-`` category
        projection. The workflow name is not known up-front because the
        protocol signature is ``load(checkpoint_id)``.

        The ``$ce-*`` category projection is eventually consistent: a
        checkpoint appended an instant ago may not yet be visible through
        the category stream. We retry for up to ``resolve_timeout`` seconds
        before giving up — callers resuming a workflow in a fresh process
        almost always have long since crossed that window, but
        write-then-resume-immediately (e.g. in tests) needs the grace
        period.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + resolve_timeout
        delay = 0.05
        while True:
            checkpoint = await self._find_checkpoint(checkpoint_id)
            if checkpoint is not None:
                return checkpoint
            if loop.time() >= deadline:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.5)
        raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        """List every checkpoint for a given workflow name."""
        checkpoints: list[WorkflowCheckpoint] = []
        async for recorded in self._read_workflow_stream(workflow_name):
            decoded = self._decode(recorded)
            if decoded is not None:
                checkpoints.append(decoded)
        return checkpoints

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete is not supported against the log-shaped KurrentDB store.

        KurrentDB is append-only; individual events are immutable. Removing a
        single checkpoint would require writing a tombstone that readers must
        interpret — a richer concept than the ``CheckpointStorage`` contract.
        Returning ``False`` matches the protocol's "no checkpoint removed"
        semantics for callers that expect best-effort deletion.
        """
        logger.warning(
            "delete(%s) ignored: KurrentDB checkpoint streams are append-only", checkpoint_id
        )
        return False

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        """Return the most recently appended checkpoint for a workflow, if any.

        Read backwards and take the first event we can decode — that is the
        latest append by stream order, which also matches the timestamp
        ordering used by the reference in-memory / file stores.
        """
        try:
            response = await self._client.read_stream(
                stream_name=workflow_checkpoint_stream(workflow_name),
                backwards=True,
            )
            # ``kurrentdbclient`` defers the NotFoundError until iteration —
            # see KurrentDBHistoryProvider.get_messages for the same pattern.
            async for recorded in response:
                decoded = self._decode(recorded)
                if decoded is not None:
                    return decoded
        except NotFoundError:
            return None
        return None

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        """Lightweight index: reads metadata only, not payloads."""
        ids: list[str] = []
        async for recorded in self._read_workflow_stream(workflow_name):
            if not recorded.metadata:
                continue
            try:
                meta = json.loads(recorded.metadata)
            except json.JSONDecodeError:
                continue
            value = meta.get(_CHECKPOINT_ID_META_KEY)
            if isinstance(value, str):
                ids.append(value)
        return ids

    # --- internals ---------------------------------------------------------

    async def _read_workflow_stream(self, workflow_name: str) -> AsyncIterator[Any]:
        try:
            response = await self._client.read_stream(
                stream_name=workflow_checkpoint_stream(workflow_name),
            )
            # NotFoundError surfaces on iteration, not at read_stream() await.
            async for recorded in response:
                yield recorded
        except NotFoundError:
            return

    async def _find_checkpoint(self, checkpoint_id: str) -> WorkflowCheckpoint | None:
        """Locate a checkpoint by id without knowing its workflow name.

        Uses the built-in ``$ce-WorkflowCheckpoint`` category stream so we scan
        once across every workflow's checkpoint stream instead of iterating
        them sequentially. Reads newest-first so retries don't repeatedly
        rescan the oldest history on each attempt (a newly-saved checkpoint
        lands at the end of the category stream).
        """
        try:
            response = await self._client.read_stream(
                stream_name=f"$ce-{WORKFLOW_CHECKPOINT_STREAM_PREFIX.rstrip('-')}",
                resolve_links=True,
                backwards=True,
            )
            async for recorded in response:
                if not recorded.metadata:
                    continue
                try:
                    meta = json.loads(recorded.metadata)
                except json.JSONDecodeError:
                    continue
                if meta.get(_CHECKPOINT_ID_META_KEY) != checkpoint_id:
                    continue
                return self._decode(recorded)
        except NotFoundError:
            return None
        return None

    def _decode(self, recorded: Any) -> WorkflowCheckpoint | None:
        """Decode a recorded event into a :class:`WorkflowCheckpoint`.

        Warnings and malformed payloads are skipped rather than propagated —
        matching the defensive reader behaviour in
        :class:`KurrentDBHistoryProvider`.
        """
        if not recorded.data:
            return None
        try:
            encoded = json.loads(recorded.data)
            decoded = decode_checkpoint_value(encoded, allowed_types=self._allowed_types)
            return WorkflowCheckpoint.from_dict(decoded)
        except (json.JSONDecodeError, WorkflowCheckpointException, ValueError, TypeError) as exc:
            logger.warning(
                "Skipping malformed WorkflowCheckpoint at %s:%s: %r",
                getattr(recorded, "stream_name", "?"),
                getattr(recorded, "stream_position", "?"),
                exc,
            )
            return None


# --- Group chat recording ---------------------------------------------------


@dataclass(slots=True)
class AgentTurnTaken:
    """Payload for an ``AgentTurnTaken`` event — one participant-response turn."""

    participant_name: str
    round_index: int
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_name": self.participant_name,
            "round_index": self.round_index,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(slots=True)
class GroupChatCompleted:
    """Payload for a ``GroupChatCompleted`` event."""

    reason: str | None
    total_rounds: int
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "total_rounds": self.total_rounds,
            "timestamp": self.timestamp.isoformat(),
        }


class KurrentDBGroupChatRecorder:
    """Records canonical turn-taking events from a workflow's event stream.

    Consumes any :class:`WorkflowEvent` iterable — typically the stream from
    ``workflow.run(stream=True)`` — and forwards every event to the caller
    while appending ``AgentTurnTaken`` / ``GroupChatCompleted`` events to a
    ``GroupChat-{chat_id}`` stream in KurrentDB.

    Works with every MAF orchestration pattern: ``GroupChatBuilder`` emits
    ``GroupChatResponseReceivedEvent`` payloads with round + participant
    directly (preferred source). For other patterns
    (``SequentialBuilder`` / ``ConcurrentBuilder`` / ``HandoffBuilder`` /
    ``MagenticBuilder`` / custom graphs), we fall back to ``executor_completed``
    events and treat each executor completion as a turn.

    Example::

        recorder = KurrentDBGroupChatRecorder(client, chat_id="demo-chat")

        async for event in recorder.record(workflow.run(message, stream=True)):
            ...  # consumer still sees every WorkflowEvent

        history = await recorder.read_history()
    """

    def __init__(self, client: AsyncKurrentDBClient, *, chat_id: str) -> None:
        self._client = client
        self._chat_id = chat_id
        self._stream = group_chat_stream(chat_id)
        self._round_counter = 0
        self._counter_primed = False

    async def record(
        self,
        events: AsyncIterable[WorkflowEvent[Any]],
        *,
        completion_reason: str | None = "completed",
    ) -> AsyncIterator[WorkflowEvent[Any]]:
        """Wrap a workflow event stream, recording turns as they happen.

        The generator yields every input event unchanged so callers can still
        drive their own UI off the stream. ``GroupChatCompleted`` is written
        once the underlying iterator is exhausted.
        """
        async for event in events:
            turn = _turn_from_event(event)
            if turn is not None:
                participant, round_index = turn
                if round_index is None:
                    # Fallback (executor_completed) path has no explicit index.
                    # Prime from the stream on first use so that restarting a
                    # recorder for an existing chat_id continues numbering
                    # instead of colliding with existing AgentTurnTaken turns.
                    if not self._counter_primed:
                        await self._prime_round_counter()
                    round_index = self._round_counter
                    self._round_counter += 1
                else:
                    self._round_counter = max(self._round_counter, round_index + 1)
                    self._counter_primed = True
                await self._append_turn(participant, round_index)
            yield event

        await self._append_completion(completion_reason)

    async def _prime_round_counter(self) -> None:
        """Read the newest ``AgentTurnTaken`` and continue numbering from it."""
        self._counter_primed = True
        try:
            response = await self._client.read_stream(stream_name=self._stream, backwards=True)
            async for recorded in response:
                if recorded.type != AGENT_TURN_TAKEN_EVENT_TYPE:
                    continue
                try:
                    payload = json.loads(recorded.data) if recorded.data else {}
                    round_index = int(payload.get("round_index", -1))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if round_index >= 0:
                    self._round_counter = round_index + 1
                    return
        except NotFoundError:
            return

    async def read_history(self) -> list[AgentTurnTaken]:
        """Read every ``AgentTurnTaken`` back from the chat's KurrentDB stream."""
        turns: list[AgentTurnTaken] = []
        try:
            response = await self._client.read_stream(stream_name=self._stream)
            # NotFoundError surfaces on iteration, not at read_stream() await.
            async for recorded in response:
                if recorded.type != AGENT_TURN_TAKEN_EVENT_TYPE:
                    continue
                try:
                    payload = json.loads(recorded.data) if recorded.data else {}
                except json.JSONDecodeError:
                    continue
                timestamp = payload.get("timestamp")
                try:
                    parsed_ts = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else datetime.now(UTC)
                except ValueError:
                    parsed_ts = datetime.now(UTC)
                try:
                    round_index = int(payload.get("round_index", 0))
                except (TypeError, ValueError):
                    # Skip turns with malformed round_index rather than aborting
                    # the whole read — same defensive stance as the JSON guard.
                    continue
                participant = payload.get("participant_name")
                if not isinstance(participant, str):
                    continue
                turns.append(
                    AgentTurnTaken(
                        participant_name=participant,
                        round_index=round_index,
                        timestamp=parsed_ts,
                    )
                )
        except NotFoundError:
            return turns
        return turns

    # --- internals ---------------------------------------------------------

    async def _append_turn(self, participant: str, round_index: int) -> None:
        turn = AgentTurnTaken(
            participant_name=participant,
            round_index=round_index,
            timestamp=datetime.now(UTC),
        )
        await self._client.append_to_stream(
            stream_name=self._stream,
            current_version=StreamState.ANY,
            events=[
                NewEvent(
                    id=uuid.uuid4(),
                    type=AGENT_TURN_TAKEN_EVENT_TYPE,
                    data=json.dumps(turn.to_dict(), separators=(",", ":")).encode("utf-8"),
                )
            ],
        )

    async def _append_completion(self, reason: str | None) -> None:
        completed = GroupChatCompleted(
            reason=reason,
            total_rounds=self._round_counter,
            timestamp=datetime.now(UTC),
        )
        await self._client.append_to_stream(
            stream_name=self._stream,
            current_version=StreamState.ANY,
            events=[
                NewEvent(
                    id=uuid.uuid4(),
                    type=GROUP_CHAT_COMPLETED_EVENT_TYPE,
                    data=json.dumps(completed.to_dict(), separators=(",", ":")).encode("utf-8"),
                )
            ],
        )


def _turn_from_event(event: WorkflowEvent[Any]) -> tuple[str, int | None] | None:
    """Extract ``(participant_name, round_index)`` from a workflow event.

    Group-chat orchestrators emit typed payloads on ``group_chat`` events that
    carry both fields; for every other orchestration pattern we treat each
    ``executor_completed`` event as a turn by the named executor.
    """
    if event.type == "group_chat":
        data = event.data
        if data is None:
            return None
        participant = getattr(data, "participant_name", None)
        if not participant:
            return None
        # Only record responses, not request-sent events — otherwise each
        # turn is double-counted (request + response on the same round).
        if type(data).__name__ != "GroupChatResponseReceivedEvent":
            return None
        round_index = getattr(data, "round_index", None)
        if round_index is None:
            return (str(participant), None)
        # Defensive: if an upstream payload ever carries a non-int round_index,
        # fall through to the recorder's fallback numbering rather than crash
        # the whole event stream.
        try:
            return (str(participant), int(round_index))
        except (TypeError, ValueError):
            return (str(participant), None)

    if event.type == "executor_completed" and event.executor_id:
        return (event.executor_id, None)

    return None
