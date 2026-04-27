"""Canonical-event ↔ KurrentDB wire serialization.

Thin adapter over the shared :mod:`kurrent_agent_schema` type registry. The
four event types specific to this integration (``AgentTransferred``,
``Rewind``, ``Compaction``, ``StateDelta``) live in :mod:`.events` and are
registered locally alongside the canonical set.

``$schema_version`` is stamped on every serialised event's metadata per
``schema/SCHEMA_v2.md §9``. It is stamped last so a caller-supplied value in
``metadata={}`` cannot forge a different wire version.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from kurrent_agent_schema import SCHEMA_VERSION
from kurrent_agent_schema.events import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES, _EventBase
from kurrentdbclient import NewEvent, RecordedEvent
from pydantic import ValidationError

from .events import AgentTransferred, Compaction, Rewind, StateDelta

logger = logging.getLogger("kurrent_google_adk._serialization")

SCHEMA_VERSION_METADATA_KEY: str = "$schema_version"
"""Metadata key stamped on every event. See SCHEMA_v2 §9."""

_ADK_LOCAL_TYPES: dict[type[_EventBase], str] = {
    AgentTransferred: "AgentTransferred",
    Rewind: "Rewind",
    Compaction: "Compaction",
    StateDelta: "StateDelta",
}

_TYPE_TO_NAME: dict[type[_EventBase], str] = {
    **EVENT_TYPE_NAMES,
    **_ADK_LOCAL_TYPES,
}
_NAME_TO_TYPE: dict[str, type[_EventBase]] = {
    **EVENT_TYPE_BY_NAME,
    **{name: cls for cls, name in _ADK_LOCAL_TYPES.items()},
}


def name_for(event: _EventBase) -> str:
    """Return the wire event-type name for a canonical or ADK-specific event."""
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
    """Serialize a canonical or ADK-specific event into a ``NewEvent``.

    Caller-supplied metadata is preserved; ``$schema_version`` is always
    stamped last and wins over any caller-supplied value so the wire
    version stays authoritative.
    """
    data = event.model_dump_json(exclude_none=True, by_alias=True).encode("utf-8")
    effective: dict[str, Any] = dict(metadata) if metadata else {}
    effective[SCHEMA_VERSION_METADATA_KEY] = SCHEMA_VERSION
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")
    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> _EventBase | None:
    """Deserialize a ``RecordedEvent`` into a known type, or ``None`` if the
    event type is unregistered *or* the payload cannot be parsed.

    Unknown event types are the reader's "skip" signal: framework-specific
    events from other integrations land here and callers pass them through.
    Parse failures on **known** types (corrupt JSON, schema drift, UTF-8
    errors) are also surfaced as ``None`` + a warning log so a single bad
    event in a long stream cannot crash ``get_session`` and break session
    resume.
    """
    cls = _NAME_TO_TYPE.get(recorded.type)
    if cls is None:
        return None
    try:
        payload = json.loads(recorded.data) if recorded.data else {}
        return cls.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as exc:
        # Log only the exception class name, not the full exception string —
        # ValidationError messages can include payload-derived field values
        # (user message text, assistant content). See Qodo review on PR #32.
        logger.warning(
            "Skipping unparseable event (type=%r, stream=%r, position=%r, error=%s)",
            recorded.type,
            recorded.stream_name,
            recorded.stream_position,
            type(exc).__name__,
        )
        return None


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode metadata JSON, or ``None`` when absent/empty."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
