"""Serialization round-trip and metadata-stamping tests.

Covers $schema_version stamping, override protection, hardened deserialize,
and round-trip equivalence for canonical + ADK-specific event types.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from kurrent_agent_schema import SCHEMA_VERSION
from kurrent_agent_schema.usage import USAGE_METADATA_KEY, TokenUsage
from kurrentdbclient import RecordedEvent

from kurrent_google_adk import _serialization
from kurrent_google_adk.events import (
    AgentTransferred,
    AssistantThinkingGenerated,
    Compaction,
    Rewind,
    SessionStarted,
    StateDelta,
    UserMessageReceived,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "schema" / "fixtures"
EVENTS_FIXTURES = FIXTURES_ROOT / "events"
USAGE_FIXTURE = FIXTURES_ROOT / "metadata" / "usage.json"

# ADK does not emit these; their fixtures aren't expected to round-trip via this adapter.
NON_ADK_FIXTURES = {
    "InterruptIssued.json",
    "InterruptResolved.json",
    "SubagentStarted.json",
    "SubagentCompleted.json",
    "SessionContinuedAs.json",
}


def _ts() -> datetime:
    return datetime(2026, 4, 26, 12, 0, tzinfo=UTC)


def _recorded(new_event) -> RecordedEvent:
    return RecordedEvent(
        type=new_event.type,
        data=new_event.data,
        metadata=new_event.metadata,
        id=new_event.id,
        stream_name="AgentSession-test",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )


# --- $schema_version stamping ------------------------------------------------


def test_serialize_stamps_schema_version_2() -> None:
    event = SessionStarted(timestamp=_ts())
    new_event = _serialization.serialize(event)
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == SCHEMA_VERSION == 2


def test_serialize_caller_metadata_cannot_override_schema_version() -> None:
    event = SessionStarted(timestamp=_ts())
    new_event = _serialization.serialize(event, metadata={"$schema_version": "v1", "$correlation_id": "abc"})
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2
    assert metadata["$correlation_id"] == "abc"


# --- Canonical round-trip ----------------------------------------------------


def test_user_message_round_trip() -> None:
    original = UserMessageReceived(
        content="hello",
        message_id="msg-1",
        author_name="alice",
        message_index=0,
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_assistant_thinking_round_trip() -> None:
    original = AssistantThinkingGenerated(
        content="planning",
        encrypted=False,
        message_id="msg-2",
        author_name="root",
        message_index=1,
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


# --- ADK-specific round-trip -------------------------------------------------


def test_agent_transferred_round_trip() -> None:
    original = AgentTransferred(from_agent="alice", to_agent="bob", timestamp=_ts())
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_rewind_round_trip() -> None:
    original = Rewind(
        rewind_before_invocation_id="inv-7",
        state_delta={"key": "value"},
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_compaction_round_trip() -> None:
    original = Compaction(
        start_timestamp=_ts(),
        end_timestamp=_ts(),
        compacted_content={"summary": "ok"},
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_state_delta_round_trip() -> None:
    original = StateDelta(delta={"k": 1}, invocation_id="inv-3", timestamp=_ts())
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


# --- Hardened deserialize ----------------------------------------------------


def test_deserialize_unknown_type_returns_none() -> None:
    bad = RecordedEvent(
        type="UnknownEventType",
        data=b"{}",
        metadata=b"",
        id=uuid.uuid4(),
        stream_name="AgentSession-test",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )
    assert _serialization.deserialize(bad) is None


def test_deserialize_malformed_json_logs_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    bad = RecordedEvent(
        type="UserMessageReceived",
        data=b"{not-json",
        metadata=b"",
        id=uuid.uuid4(),
        stream_name="AgentSession-test",
        stream_position=42,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )
    with caplog.at_level("WARNING", logger="kurrent_google_adk._serialization"):
        assert _serialization.deserialize(bad) is None
    assert any("Skipping unparseable event" in rec.message for rec in caplog.records)
    # The log line should carry the exception class name, not the raw exception
    # message — Qodo flagged that ValidationError messages can leak payload
    # content into WARNING logs.
    assert any("error=JSONDecodeError" in rec.message for rec in caplog.records)


def test_deserialize_schema_mismatch_logs_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    # UserMessageReceived requires `message_index: int` and `timestamp: datetime`.
    bad = RecordedEvent(
        type="UserMessageReceived",
        data=b'{"content": "hi"}',
        metadata=b"",
        id=uuid.uuid4(),
        stream_name="AgentSession-test",
        stream_position=43,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )
    with caplog.at_level("WARNING", logger="kurrent_google_adk._serialization"):
        assert _serialization.deserialize(bad) is None
    assert any("Skipping unparseable event" in rec.message for rec in caplog.records)
    assert any("error=ValidationError" in rec.message for rec in caplog.records)


# --- Fixture round-trip ------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    sorted(p for p in EVENTS_FIXTURES.glob("*.json") if p.name not in NON_ADK_FIXTURES),
    ids=lambda p: p.name,
)
def test_event_fixture_round_trip(fixture: Path) -> None:
    """Each canonical fixture deserialises into the matching canonical type
    and serialises back to byte-equivalent JSON (modulo key ordering)."""
    expected = json.loads(fixture.read_text(encoding="utf-8"))
    event_type_name = fixture.stem
    cls = _serialization._NAME_TO_TYPE[event_type_name]
    event = cls.model_validate(expected)
    actual = json.loads(event.model_dump_json(exclude_none=True, by_alias=True))
    assert actual == expected, f"{fixture.name} drifted on round-trip"


def test_usage_fixture_round_trip() -> None:
    expected = json.loads(USAGE_FIXTURE.read_text(encoding="utf-8"))
    usage = TokenUsage.model_validate(expected)
    actual = json.loads(usage.model_dump_json(exclude_none=True, by_alias=True))
    assert actual == expected


def test_usage_metadata_key_constant() -> None:
    assert USAGE_METADATA_KEY == "$usage"


def test_credential_saved_round_trip() -> None:
    from datetime import datetime, timezone

    from kurrent_google_adk._serialization import deserialize, name_for, serialize
    from kurrent_google_adk.events import CredentialSaved

    event = CredentialSaved(
        credential_key="oauth2:scope=read",
        credential="AQABAGRlYWRiZWVm",  # dummy base64
        timestamp=datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc),
    )
    assert name_for(event) == "CredentialSaved"

    new_event = serialize(event)
    assert new_event.type == "CredentialSaved"

    class _Recorded:
        type = new_event.type
        data = new_event.data
        metadata = new_event.metadata
        stream_name = "Credentials-app-user"
        stream_position = 0

    decoded = deserialize(_Recorded())  # type: ignore[arg-type]
    assert isinstance(decoded, CredentialSaved)
    assert decoded.credential_key == event.credential_key
    assert decoded.credential == event.credential
    assert decoded.timestamp == event.timestamp
