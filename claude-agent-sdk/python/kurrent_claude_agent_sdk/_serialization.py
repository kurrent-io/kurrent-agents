"""Canonical-event ↔ KurrentDB wire serialization.

Thin adapter over :mod:`kurrent_agent_schema`'s shared type registry. The one
type specific to this integration is :class:`ClaudeSDKEntry` — the verbatim
envelope — which is registered locally alongside the canonical set.

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
from kurrent_agent_schema.events import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES
from kurrentdbclient import NewEvent, RecordedEvent
from pydantic import BaseModel, ValidationError

from .events import ClaudeSDKEntry

logger = logging.getLogger("kurrent_claude_agent_sdk._serialization")

SCHEMA_VERSION_METADATA_KEY: str = "$schema_version"
"""Metadata key stamped on every event. See SCHEMA_v2 §9."""

CLAUDE_SDK_ENTRY_EVENT_TYPE: str = "ClaudeSDKEntry"
"""Wire event-type name for the verbatim envelope. Framework-specific;
non-Claude-SDK readers should pass this through (skip) on read."""

# Local merge of the shared canonical registry plus our one extra type. Kept as
# read-only views to avoid mutating the shared dicts.
_TYPE_TO_NAME: dict[type[BaseModel], str] = {
    **EVENT_TYPE_NAMES,
    ClaudeSDKEntry: CLAUDE_SDK_ENTRY_EVENT_TYPE,
}
_NAME_TO_TYPE: dict[str, type[BaseModel]] = {
    **EVENT_TYPE_BY_NAME,
    CLAUDE_SDK_ENTRY_EVENT_TYPE: ClaudeSDKEntry,
}


def _name_for(event: BaseModel) -> str:
    name = _TYPE_TO_NAME.get(type(event))
    if name is None:
        raise ValueError(f"Unknown event type: {type(event).__name__}")
    return name


def serialize(
    event: BaseModel,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a canonical or ``ClaudeSDKEntry`` event into a ``NewEvent``.

    Caller-supplied metadata is preserved; ``$schema_version`` is always
    stamped last and wins over any caller-supplied value so the wire version
    stays authoritative.
    """
    data = event.model_dump_json(exclude_none=True, by_alias=True).encode("utf-8")

    effective: dict[str, Any] = dict(metadata) if metadata else {}
    effective[SCHEMA_VERSION_METADATA_KEY] = SCHEMA_VERSION
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")

    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> BaseModel | None:
    """Deserialize a ``RecordedEvent`` into a known type, or ``None`` if the
    event type is unregistered *or* the payload cannot be parsed.

    Unknown event types are the reader's "skip" signal: framework-specific
    events from other integrations land here and callers pass them through.
    Parse failures on **known** types (corrupt JSON, schema drift, UTF-8
    errors) are also surfaced as ``None`` + a warning log so a single bad
    event in a long stream can't crash ``KurrentDBSessionStore.load`` and
    break session resume. Mirrors the MAF-Python hardening applied in
    ``KurrentDBHistoryProvider.get_messages`` (commit ``a49e04c``).
    """
    cls = _NAME_TO_TYPE.get(recorded.type)
    if cls is None:
        return None
    try:
        payload = json.loads(recorded.data) if recorded.data else {}
        return cls.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as exc:
        logger.warning(
            "Skipping unparseable event (type=%r, stream=%r, position=%r): %s",
            recorded.type,
            recorded.stream_name,
            recorded.stream_position,
            exc,
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
