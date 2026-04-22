"""Thin adapter that packages canonical :mod:`kurrent_agent_schema` events into
KurrentDB ``NewEvent``s.

Uses the shared :data:`EVENT_TYPE_NAMES` / :data:`EVENT_TYPE_BY_NAME` registries
for wire naming and stamps ``$schema_version`` on every event's metadata per
``schema/SCHEMA_v2.md §9``. Mirrors ``Kurrent.AgentFramework.Serialization.EventSerializer``
on the .NET side.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from kurrent_agent_schema import SCHEMA_VERSION
from kurrent_agent_schema.events import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES, _EventBase
from kurrentdbclient import NewEvent, RecordedEvent

SCHEMA_VERSION_METADATA_KEY: str = "$schema_version"
"""Metadata key stamped on every canonical event. See SCHEMA_v2 §9."""


def _name_for(event: _EventBase) -> str:
    name = EVENT_TYPE_NAMES.get(type(event))
    if name is None:
        raise ValueError(f"Unknown event type: {type(event).__name__}")
    return name


def serialize(
    event: _EventBase,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a canonical event into a KurrentDB ``NewEvent``.

    Caller-supplied metadata is preserved; ``$schema_version`` is always stamped
    last and wins over any caller-supplied value so the wire version stays
    authoritative.
    """
    payload = json.loads(event.model_dump_json(exclude_none=True, by_alias=True))
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    effective: dict[str, Any] = dict(metadata) if metadata else {}
    effective[SCHEMA_VERSION_METADATA_KEY] = SCHEMA_VERSION
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")

    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> _EventBase | None:
    """Deserialize a ``RecordedEvent`` into a canonical event, or ``None`` if
    the event type is not in the canonical map (framework-specific or unknown
    types are skipped by readers)."""
    cls = EVENT_TYPE_BY_NAME.get(recorded.type)
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
