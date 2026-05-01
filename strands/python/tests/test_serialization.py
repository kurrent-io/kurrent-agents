"""Wire-format tests for the Strands serializer adapter.

Pins the on-the-wire JSON shape and ``$schema_version`` metadata stamping for
events emitted via :mod:`kurrent_strands._serialization`. Exercises both
canonical proto events (from the shared ``kurrent_agent_schema`` package) and
framework-specific Pydantic events (``StrandsAgentState``, ``MessageRedacted``).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from kurrent_agent_schema import (
    UserMessageReceived,
)
from kurrentdbclient import RecordedEvent

from kurrent_strands import _serialization as serialization
from kurrent_strands._strands_events import StrandsAgentState

TS = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


def _recorded_from(new_event: object, *, stream_name: str = "AgentSession-s1") -> RecordedEvent:
    """Build a ``RecordedEvent`` from a freshly-serialised ``NewEvent`` so the
    round-trip path can be exercised without a live KurrentDB instance."""
    return RecordedEvent(
        type=new_event.type,
        data=new_event.data,
        metadata=new_event.metadata,
        content_type="application/json",
        id=new_event.id,
        stream_name=stream_name,
        stream_position=0,
        commit_position=0,
        prepare_position=0,
    )


# --- Canonical proto events --------------------------------------------------


def test_canonical_proto_event_uses_shared_registry_type_name() -> None:
    evt = UserMessageReceived(content="hi", message_index=0, timestamp=TS)
    new_event = serialization.serialize(evt)
    assert new_event.type == "UserMessageReceived"


def test_canonical_proto_event_round_trips_through_recorded_event() -> None:
    evt = UserMessageReceived(content="hi there", message_index=7, timestamp=TS)
    new_event = serialization.serialize(evt)
    restored = serialization.deserialize(_recorded_from(new_event))
    assert isinstance(restored, UserMessageReceived)
    assert restored.content == "hi there"
    assert restored.message_index == 7


def test_canonical_proto_event_wire_payload_is_snake_case_json() -> None:
    evt = UserMessageReceived(content="hi", message_index=0, timestamp=TS)
    new_event = serialization.serialize(evt)
    payload = json.loads(new_event.data)
    assert payload["content"] == "hi"
    assert payload["message_index"] == 0
    assert payload["timestamp"] == "2026-04-30T12:00:00Z"


# --- $schema_version metadata stamping (SCHEMA_v2 §9) -----------------------


def test_schema_version_is_stamped_on_metadata() -> None:
    evt = UserMessageReceived(content="hi", message_index=0, timestamp=TS)
    new_event = serialization.serialize(evt)
    metadata = json.loads(new_event.metadata)
    assert metadata == {"$schema_version": 2}


def test_caller_metadata_is_preserved_alongside_schema_version() -> None:
    evt = UserMessageReceived(content="hi", message_index=0, timestamp=TS)
    new_event = serialization.serialize(evt, metadata={"request_id": "r1"})
    metadata = json.loads(new_event.metadata)
    assert metadata == {"request_id": "r1", "$schema_version": 2}


def test_caller_cannot_override_schema_version() -> None:
    """The writer's ``$schema_version`` always wins over caller-supplied values
    so the wire version stays authoritative (stamped last)."""
    evt = UserMessageReceived(content="hi", message_index=0, timestamp=TS)
    new_event = serialization.serialize(evt, metadata={"$schema_version": 99})
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2


# --- Strands-specific Pydantic events ----------------------------------------


def test_strands_specific_event_uses_local_registry_type_name() -> None:
    evt = StrandsAgentState(agent_id="a", timestamp=TS)
    new_event = serialization.serialize(evt)
    assert new_event.type == "StrandsAgentState"


def test_strands_specific_event_round_trips() -> None:
    evt = StrandsAgentState(
        agent_id="a",
        state={"k": "v"},
        conversation_manager_state={"summary": "abc"},
        internal_state={"interrupt_state": {"pending": True}},
        timestamp=TS,
    )
    new_event = serialization.serialize(evt)
    restored = serialization.deserialize(_recorded_from(new_event))
    assert isinstance(restored, StrandsAgentState)
    assert restored.agent_id == "a"
    assert restored.state == {"k": "v"}
    assert restored.conversation_manager_state == {"summary": "abc"}
    assert restored.internal_state == {"interrupt_state": {"pending": True}}


def test_strands_specific_event_also_carries_schema_version() -> None:
    """Framework-specific events ride in the same stream as canonical ones,
    so they get the same ``$schema_version`` stamp for uniformity."""
    evt = StrandsAgentState(agent_id="a", timestamp=TS)
    new_event = serialization.serialize(evt)
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2


# --- Forward compatibility ---------------------------------------------------


def test_unknown_event_type_returns_none_on_deserialize() -> None:
    """Readers must skip events whose type name isn't registered (canonical
    schema growth, framework-specific events from other integrations)."""
    recorded = RecordedEvent(
        type="UnknownEvent",
        data=b"{}",
        metadata=b"",
        content_type="application/json",
        id=uuid.uuid4(),
        stream_name="AgentSession-s1",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
    )
    assert serialization.deserialize(recorded) is None
