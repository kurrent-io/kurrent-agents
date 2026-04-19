"""Unit tests for canonical event (de)serialisation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kurrentdbclient import RecordedEvent

from kurrent_google_adk._schema.events import (
    ADK_EXTENSION_KEY,
    SessionStarted,
    UserMessageReceived,
)
from kurrent_google_adk._serialization import (
    deserialize,
    name_for,
    read_metadata,
    serialize,
)


def _make_recorded(type_: str, data: bytes, metadata: bytes = b"") -> RecordedEvent:
    """Build a RecordedEvent with the minimum fields the codec cares about."""
    import uuid

    return RecordedEvent(
        type=type_,
        data=data,
        metadata=metadata,
        content_type="application/json",
        id=uuid.uuid4(),
        stream_name="test",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
    )


def test_name_for_canonical_event() -> None:
    event = SessionStarted(timestamp=datetime(2026, 4, 19, tzinfo=UTC))
    assert name_for(event) == "SessionStarted"


def test_serialize_produces_snake_case_json() -> None:
    event = UserMessageReceived(
        content="hello",
        message_id="m1",
        author_name="alice",
        message_index=0,
        timestamp=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
    )
    new_event = serialize(event)
    assert new_event.type == "UserMessageReceived"
    data = json.loads(new_event.data)
    assert data == {
        "content": "hello",
        "message_id": "m1",
        "author_name": "alice",
        "message_index": 0,
        "timestamp": "2026-04-19T12:00:00Z",
    }


def test_serialize_attaches_metadata() -> None:
    event = SessionStarted(timestamp=datetime(2026, 4, 19, tzinfo=UTC))
    new_event = serialize(event, metadata={"$usage": {"input_tokens": 10}})
    assert json.loads(new_event.metadata) == {"$usage": {"input_tokens": 10}}


def test_roundtrip_via_recorded_event() -> None:
    original = UserMessageReceived(
        content="hello",
        message_id="m1",
        message_index=0,
        timestamp=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
        extensions={ADK_EXTENSION_KEY: {"invocation_id": "inv_1"}},
    )
    new_event = serialize(original)
    recorded = _make_recorded(new_event.type, new_event.data, new_event.metadata)
    reloaded = deserialize(recorded)
    assert isinstance(reloaded, UserMessageReceived)
    assert reloaded.content == "hello"
    assert reloaded.extensions == {ADK_EXTENSION_KEY: {"invocation_id": "inv_1"}}


def test_deserialize_unknown_type_returns_none() -> None:
    recorded = _make_recorded("UnknownFutureType", b"{}")
    assert deserialize(recorded) is None


def test_read_metadata_handles_empty_and_invalid() -> None:
    empty = _make_recorded("SessionStarted", b"{}", b"")
    assert read_metadata(empty) is None
    invalid = _make_recorded("SessionStarted", b"{}", b"not-json")
    assert read_metadata(invalid) is None
    valid = _make_recorded("SessionStarted", b"{}", b'{"$usage": {"input_tokens": 5}}')
    assert read_metadata(valid) == {"$usage": {"input_tokens": 5}}
