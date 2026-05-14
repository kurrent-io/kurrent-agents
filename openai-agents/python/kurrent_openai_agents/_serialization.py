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
import logging
import uuid
from typing import Any

from google.protobuf import json_format
from google.protobuf.message import Message as ProtoMessage
from kurrent_agent_schema import (
    EVENT_TYPE_BY_NAME,
    EVENT_TYPE_NAMES,
    SCHEMA_VERSION,
    from_json,
    to_json,
)
from kurrentdbclient import NewEvent, RecordedEvent
from pydantic import BaseModel, ValidationError

from ._openai_events import OpenAIItem

logger = logging.getLogger("kurrent_openai_agents._serialization")

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


def serialize_for_multi_append(
    event: ProtoMessage | BaseModel,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize for the v2 multi-stream append path.

    KurrentDB's gRPC ``AppendSession`` (used by ``multi_append_to_stream``)
    requires event metadata to be a JSON document with **string-only values**
    (see ``kurrentdbclient.v2streams._metadata_to_properties``). The single-
    stream ``append_to_stream`` path accepts any JSON, so the regular
    ``serialize`` stamps ``$schema_version`` as int ``2``. This helper stamps
    it as the string ``"2"`` and rejects any caller-supplied non-string values.
    """
    data = _encode_event_data(event)

    effective: dict[str, Any] = dict(metadata) if metadata else {}
    for key, value in effective.items():
        if not isinstance(value, str):
            raise ValueError(
                f"multi-append metadata values must be strings; got "
                f"{key}={value!r} ({type(value).__name__})"
            )
    effective[SCHEMA_VERSION_METADATA_KEY] = str(SCHEMA_VERSION)
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")

    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> ProtoMessage | BaseModel | None:
    """Deserialize a ``RecordedEvent`` into a canonical proto event or an
    OpenAI-specific Pydantic event.

    Returns ``None`` when:

    - The event type is unknown to this integration (forward-compat /
      cross-framework tolerance).
    - The payload is malformed for an otherwise-known type (invalid UTF-8,
      invalid JSON, schema-mismatch). A single corrupt record must not
      block the whole session read; a warning is logged with enough
      identification to find the offending event in KurrentDB.
    """
    proto_cls = EVENT_TYPE_BY_NAME.get(recorded.type)
    if proto_cls is not None:
        if not recorded.data:
            return proto_cls()
        try:
            decoded = recorded.data.decode("utf-8")
            return from_json(proto_cls, decoded)
        except (UnicodeDecodeError, json_format.ParseError, ValueError) as exc:
            _log_malformed(recorded, exc)
            return None

    pydantic_cls = _OPENAI_NAME_TO_TYPE.get(recorded.type)
    if pydantic_cls is not None:
        try:
            payload = json.loads(recorded.data) if recorded.data else {}
            return pydantic_cls.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as exc:
            _log_malformed(recorded, exc)
            return None

    return None


def _log_malformed(recorded: RecordedEvent, exc: Exception) -> None:
    """Log a single malformed-event warning with stream coordinates.

    Includes ``stream_name`` and ``stream_position`` so the offending event
    can be located via KurrentDB's UI / API. The exception type is logged
    rather than the full message so a flood of similar corruption does not
    drown the log; full repro requires reading the event by position.
    """
    logger.warning(
        "Skipping malformed event %r at %s@%d: %s",
        recorded.type,
        getattr(recorded, "stream_name", "?"),
        getattr(recorded, "stream_position", -1),
        type(exc).__name__,
    )


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode KurrentDB event metadata JSON, or ``None`` when absent/invalid."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
