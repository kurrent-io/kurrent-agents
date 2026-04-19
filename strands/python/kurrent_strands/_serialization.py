"""Canonical-event ↔ KurrentDB wire serialization.

Same layout as the other integrations: snake_case JSON, event type name from
the simple class name, optional KurrentDB event metadata carried separately.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from kurrentdbclient import NewEvent, RecordedEvent

from ._schema import events as _events
from ._schema.events import _EventBase as CanonicalEvent


_NAME_TO_TYPE: dict[str, type[CanonicalEvent]] = {
    # Canonical events (shared with ADK + AFW).
    "SessionStarted": _events.SessionStarted,
    "SessionEnded": _events.SessionEnded,
    "UserMessageReceived": _events.UserMessageReceived,
    "AssistantTextGenerated": _events.AssistantTextGenerated,
    "AssistantToolCallsGenerated": _events.AssistantToolCallsGenerated,
    "ToolResultReceived": _events.ToolResultReceived,
    "FactRetained": _events.FactRetained,
    "ArtifactVersionCreated": _events.ArtifactVersionCreated,
    "EvalRunStarted": _events.EvalRunStarted,
    "TurnScored": _events.TurnScored,
    "EvalRunCompleted": _events.EvalRunCompleted,
    # ADK-specific event types (SCHEMA.md §4) — registered here so Strands
    # readers can deserialise them to ``None`` equivalents without crashing
    # when they encounter a stream an ADK agent also wrote to. Strands does
    # not emit these.
    "AgentTransferred": _events.AgentTransferred,
    "Rewind": _events.Rewind,
    "Compaction": _events.Compaction,
    "StateDelta": _events.StateDelta,
    # Strands-specific event types (DESIGN.md §5).
    "StrandsAgentState": _events.StrandsAgentState,
    "MessageRedacted": _events.MessageRedacted,
}

_TYPE_TO_NAME: dict[type[CanonicalEvent], str] = {
    cls: name for name, cls in _NAME_TO_TYPE.items()
}


def name_for(event: CanonicalEvent) -> str:
    """Return the KurrentDB event type name for a canonical event instance."""
    name = _TYPE_TO_NAME.get(type(event))
    if name is None:
        raise ValueError(f"Unknown event type: {type(event).__name__}")
    return name


def serialize(
    event: CanonicalEvent,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a canonical event to a KurrentDB ``NewEvent``."""
    payload = event.model_dump(mode="json", exclude_none=True)
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    metadata_bytes = (
        json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        if metadata
        else b""
    )
    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> CanonicalEvent | None:
    """Deserialize a ``RecordedEvent`` to a canonical event.

    Returns ``None`` if the event type isn't registered — lets readers skip
    unknown types (forward compatibility and cross-framework tolerance).
    """
    cls = _NAME_TO_TYPE.get(recorded.type)
    if cls is None:
        return None
    payload = json.loads(recorded.data) if recorded.data else {}
    return cls.model_validate(payload)


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode KurrentDB event metadata JSON, or ``None`` when absent/invalid."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
