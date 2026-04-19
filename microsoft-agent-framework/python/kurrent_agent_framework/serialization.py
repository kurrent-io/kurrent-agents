"""Event serialization to/from KurrentDB ``NewEvent`` and ``RecordedEvent``.

Mirrors the C# ``EventSerializer`` and ``EventTypeMap``. JSON is encoded as
snake_case UTF-8 bytes, with optional metadata stored as a separate JSON object
in the event metadata slot.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from kurrentdbclient import NewEvent, RecordedEvent

from . import events as _events
from .events import _EventBase

# Bidirectional CLR-name <-> Pydantic-class map. Keep in sync with
# Kurrent.AgentFramework.Serialization.EventTypeMap on the C# side.
_NAME_TO_TYPE: dict[str, type[_EventBase]] = {
    "SessionStarted":              _events.SessionStarted,
    "SessionEnded":                _events.SessionEnded,
    "UserMessageReceived":         _events.UserMessageReceived,
    "AssistantTextGenerated":      _events.AssistantTextGenerated,
    "AssistantToolCallsGenerated": _events.AssistantToolCallsGenerated,
    "ToolResultReceived":          _events.ToolResultReceived,
    "FactRetained":                _events.FactRetained,
    "TokenUsageRecorded":          _events.TokenUsageRecorded,
    "EvalRunStarted":              _events.EvalRunStarted,
    "TurnScored":                  _events.TurnScored,
    "EvalRunCompleted":            _events.EvalRunCompleted,
}

_TYPE_TO_NAME: dict[type[_EventBase], str] = {v: k for k, v in _NAME_TO_TYPE.items()}


def _name_for(event: _EventBase) -> str:
    name = _TYPE_TO_NAME.get(type(event))
    if name is None:
        raise ValueError(f"Unknown event type: {type(event).__name__}")
    return name


def serialize(
    event: _EventBase,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a Pydantic event into a KurrentDB ``NewEvent``."""
    payload = event.model_dump(mode="json", exclude_none=True)
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    metadata_bytes = (
        json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        if metadata
        else b""
    )
    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> _EventBase | None:
    """Deserialize a ``RecordedEvent`` into a Pydantic event, or ``None`` if unknown."""
    cls = _NAME_TO_TYPE.get(recorded.type)
    if cls is None:
        return None
    payload = json.loads(recorded.data) if recorded.data else {}
    return cls.model_validate(payload)


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode metadata JSON, or ``None`` when absent/empty."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except json.JSONDecodeError:
        return None
