"""Canonical-event ↔ KurrentDB wire serialization.

Routes canonical events through the shared :mod:`kurrent_agent_schema`
package (protobuf-backed, snake_case JSON via the sanctioned :func:`to_json`
/ :func:`from_json` helpers) and OpenAI-Agents-specific framework events
through their local Pydantic models. Stamps ``$schema_version`` on every
event's metadata per ``schema/SCHEMA_v2.md §9``.

Mirrors :mod:`kurrent_strands._serialization` and
:mod:`kurrent_agent_framework.serialization` on the canonical path.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from google.protobuf.message import Message as ProtoMessage
from kurrent_agent_schema import (
    EVENT_TYPE_BY_NAME,
    EVENT_TYPE_NAMES,
    SCHEMA_VERSION,
    from_json,
    to_json,
)
from kurrentdbclient import NewEvent, RecordedEvent
from pydantic import BaseModel

from ._openai_events import OpenAIItem

SCHEMA_VERSION_METADATA_KEY: str = "$schema_version"
"""Metadata key stamped on every event. See ``SCHEMA_v2.md §9``."""

_OPENAI_NAME_TO_TYPE: dict[str, type[BaseModel]] = {
    "OpenAIItem": OpenAIItem,
}
_OPENAI_TYPE_TO_NAME: dict[type[BaseModel], str] = {
    cls: name for name, cls in _OPENAI_NAME_TO_TYPE.items()
}


def _name_for(event: ProtoMessage | BaseModel) -> str:
    if isinstance(event, ProtoMessage):
        name = EVENT_TYPE_NAMES.get(type(event))
        if name is not None:
            return name
    elif isinstance(event, BaseModel):
        name = _OPENAI_TYPE_TO_NAME.get(type(event))
        if name is not None:
            return name
    raise ValueError(f"Unknown event type: {type(event).__name__}")


def _encode_event_data(event: ProtoMessage | BaseModel) -> bytes:
    if isinstance(event, ProtoMessage):
        return to_json(event).encode("utf-8")
    return event.model_dump_json(exclude_none=True).encode("utf-8")


def serialize(
    event: ProtoMessage | BaseModel,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a canonical or OpenAI-specific event into a KurrentDB ``NewEvent``.

    Caller-supplied metadata is preserved; ``$schema_version`` is always
    stamped last so the wire version stays authoritative.
    """
    data = _encode_event_data(event)

    effective: dict[str, Any] = dict(metadata) if metadata else {}
    effective[SCHEMA_VERSION_METADATA_KEY] = SCHEMA_VERSION
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")

    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> ProtoMessage | BaseModel | None:
    """Deserialize a ``RecordedEvent`` into a canonical proto event or an
    OpenAI-specific Pydantic event. Returns ``None`` for unknown event types
    (forward-compat / cross-framework tolerance)."""
    proto_cls = EVENT_TYPE_BY_NAME.get(recorded.type)
    if proto_cls is not None:
        if not recorded.data:
            return proto_cls()
        return from_json(proto_cls, recorded.data.decode("utf-8"))

    pydantic_cls = _OPENAI_NAME_TO_TYPE.get(recorded.type)
    if pydantic_cls is not None:
        payload = json.loads(recorded.data) if recorded.data else {}
        return pydantic_cls.model_validate(payload)

    return None


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode KurrentDB event metadata JSON, or ``None`` when absent/invalid."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
