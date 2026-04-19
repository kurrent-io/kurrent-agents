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
    # Canonical events (shared with ADK / Strands / AFW).
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
    # ADK-specific — registered so we can deserialise streams that an ADK
    # agent also wrote to, even though we never emit these.
    "AgentTransferred": _events.AgentTransferred,
    "Rewind": _events.Rewind,
    "Compaction": _events.Compaction,
    "StateDelta": _events.StateDelta,
    # Strands-specific — same reasoning.
    "StrandsAgentState": _events.StrandsAgentState,
    "MessageRedacted": _events.MessageRedacted,
    # OpenAI Agents-specific.
    "OpenAIItem": _events.OpenAIItem,
}

_TYPE_TO_NAME: dict[type[CanonicalEvent], str] = {
    cls: name for name, cls in _NAME_TO_TYPE.items()
}


def name_for(event: CanonicalEvent) -> str:
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
    cls = _NAME_TO_TYPE.get(recorded.type)
    if cls is None:
        return None
    payload = json.loads(recorded.data) if recorded.data else {}
    return cls.model_validate(payload)


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
