"""Serialization-layer tests.

Covers the thin adapter over :mod:`kurrent_agent_schema`'s shared registry plus
the one integration-specific event (``ClaudeSDKEntry``):

* ``$schema_version`` is stamped on every event, wins over caller-supplied.
* ``ClaudeSDKEntry`` and canonical events both round-trip through
  ``serialize`` → ``NewEvent`` → (fake ``RecordedEvent``) → ``deserialize``.
* The repository fixture ``schema/fixtures/metadata/usage.json`` round-trips
  byte-equivalent through :class:`TokenUsage` (DEV-1543).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from kurrent_agent_schema import (
    SCHEMA_VERSION,
    USAGE_METADATA_KEY,
    AssistantTextGenerated,
    SessionStarted,
    TokenUsage,
)
from kurrentdbclient import RecordedEvent

from kurrent_claude_agent_sdk import ClaudeSDKEntry
from kurrent_claude_agent_sdk._serialization import (
    SCHEMA_VERSION_METADATA_KEY,
    deserialize,
    read_metadata,
    serialize,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
USAGE_FIXTURE = REPO_ROOT / "schema" / "fixtures" / "metadata" / "usage.json"


def _as_recorded(new_event: Any) -> RecordedEvent:
    """Build a ``RecordedEvent`` from a ``NewEvent`` for round-trip tests."""
    return RecordedEvent(
        type=new_event.type,
        data=new_event.data,
        metadata=new_event.metadata,
        content_type="application/json",
        id=new_event.id,
        stream_name="test-stream",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
    )


class TestSchemaVersionStamping:
    def test_schema_version_stamped_on_every_event(self) -> None:
        event = AssistantTextGenerated(
            content="hello",
            message_index=0,
            timestamp=datetime.now(UTC),
        )
        new_event = serialize(event)
        meta = json.loads(new_event.metadata)
        assert meta[SCHEMA_VERSION_METADATA_KEY] == SCHEMA_VERSION

    def test_caller_metadata_is_preserved(self) -> None:
        event = AssistantTextGenerated(
            content="hello",
            message_index=0,
            timestamp=datetime.now(UTC),
        )
        new_event = serialize(event, metadata={"$usage": {"input_tokens": 1}})
        meta = json.loads(new_event.metadata)
        assert meta["$usage"] == {"input_tokens": 1}
        assert meta[SCHEMA_VERSION_METADATA_KEY] == SCHEMA_VERSION

    def test_schema_version_wins_over_caller_supplied(self) -> None:
        """Caller-supplied ``$schema_version`` must not forge the wire version."""
        event = AssistantTextGenerated(
            content="hello",
            message_index=0,
            timestamp=datetime.now(UTC),
        )
        new_event = serialize(event, metadata={SCHEMA_VERSION_METADATA_KEY: 999})
        meta = json.loads(new_event.metadata)
        assert meta[SCHEMA_VERSION_METADATA_KEY] == SCHEMA_VERSION


class TestRoundTrip:
    def test_canonical_session_started_round_trips(self) -> None:
        event = SessionStarted(
            app_name="app",
            user_id="alice",
            timestamp=datetime.now(UTC),
        )
        recorded = _as_recorded(serialize(event))
        reconstructed = deserialize(recorded)
        assert isinstance(reconstructed, SessionStarted)
        assert reconstructed.app_name == "app"
        assert reconstructed.user_id == "alice"

    def test_claude_sdk_entry_round_trips_verbatim(self) -> None:
        """The one SDK invariant: ``load(append(entries)) == entries``.

        ``raw_entry`` must come back deep-equal with the full JSONL dict
        preserved.
        """
        raw = {
            "type": "user",
            "uuid": "e1",
            "timestamp": "2026-04-23T06:46:24.596Z",
            "message": {"role": "user", "content": "Hi"},
            "sessionId": "sess-1",
            "parentUuid": None,
        }
        event = ClaudeSDKEntry(
            entry_type="user",
            entry_uuid="e1",
            entry_timestamp="2026-04-23T06:46:24.596Z",
            raw_entry=raw,
            subpath=None,
            timestamp=datetime.now(UTC),
            extensions={"claude_sdk": {"project_key": "proj-1"}},
        )
        recorded = _as_recorded(serialize(event))
        assert recorded.type == "ClaudeSDKEntry"
        reconstructed = deserialize(recorded)
        assert isinstance(reconstructed, ClaudeSDKEntry)
        assert reconstructed.raw_entry == raw
        assert reconstructed.extensions == {"claude_sdk": {"project_key": "proj-1"}}

    def test_unknown_event_type_deserialises_to_none(self) -> None:
        """Readers must pass through events they don't own by returning None."""
        recorded = RecordedEvent(
            type="SomeFutureEvent",
            data=b"{}",
            metadata=b"",
            content_type="application/json",
            id=uuid.uuid4(),
            stream_name="test-stream",
            stream_position=0,
            commit_position=0,
            prepare_position=0,
        )
        assert deserialize(recorded) is None

    def test_malformed_json_deserialises_to_none(self) -> None:
        """Corrupt JSON for a *known* event type must not crash callers —
        one bad record in a long stream can't break ``--resume``.
        """
        recorded = RecordedEvent(
            type="SessionStarted",
            data=b"{not valid json",
            metadata=b"",
            content_type="application/json",
            id=uuid.uuid4(),
            stream_name="AgentSession-test",
            stream_position=3,
            commit_position=0,
            prepare_position=0,
        )
        assert deserialize(recorded) is None

    def test_schema_mismatch_deserialises_to_none(self) -> None:
        """A payload that parses as JSON but fails Pydantic validation (e.g.
        a field type change between versions) must also skip rather than
        raise — matches MAF-Python hardening from PR #15."""
        recorded = RecordedEvent(
            type="ClaudeSDKEntry",
            data=b'{"entry_type": 42}',  # entry_type must be str, and required fields missing.
            metadata=b"",
            content_type="application/json",
            id=uuid.uuid4(),
            stream_name="AgentSession-test",
            stream_position=5,
            commit_position=0,
            prepare_position=0,
        )
        assert deserialize(recorded) is None

    def test_read_metadata_decodes_and_handles_empty(self) -> None:
        event = AssistantTextGenerated(
            content="hi", message_index=0, timestamp=datetime.now(UTC)
        )
        recorded = _as_recorded(serialize(event, metadata={"custom": 42}))
        meta = read_metadata(recorded)
        assert meta is not None
        assert meta["custom"] == 42
        assert meta[SCHEMA_VERSION_METADATA_KEY] == SCHEMA_VERSION


class TestUsageFixtureRoundTrip:
    """DEV-1543: round-trip the repository ``$usage`` fixture through
    :class:`TokenUsage` to lock the byte-equivalent shape across Python and
    .NET."""

    def test_usage_fixture_loads_into_token_usage(self) -> None:
        fixture = json.loads(USAGE_FIXTURE.read_text(encoding="utf-8"))
        token_usage = TokenUsage.model_validate(fixture)
        assert token_usage.input_tokens == 1507
        assert token_usage.output_tokens == 203
        assert token_usage.total_tokens == 1710
        assert token_usage.cached_input_tokens == 0
        assert token_usage.reasoning_tokens == 0
        assert token_usage.model == "claude-sonnet-4-6"
        assert token_usage.additional_counts == {
            "cache_creation_input_tokens": 40136,
            "service_tier": "standard",
        }

    def test_usage_fixture_round_trips_byte_equivalent(self) -> None:
        """Serialise through the integration's ``$usage``-bearing event and
        verify the fixture survives round-trip untouched."""
        fixture = json.loads(USAGE_FIXTURE.read_text(encoding="utf-8"))
        token_usage = TokenUsage.model_validate(fixture)

        event = AssistantTextGenerated(
            content="hello",
            message_index=0,
            timestamp=datetime.now(UTC),
        )
        new_event = serialize(
            event,
            metadata={
                USAGE_METADATA_KEY: token_usage.model_dump(
                    mode="json", exclude_none=True, by_alias=True
                )
            },
        )
        meta = json.loads(new_event.metadata)
        assert meta[USAGE_METADATA_KEY] == fixture

        # And the reverse: reading the metadata back must reproduce the fixture.
        recorded = _as_recorded(new_event)
        round_tripped = read_metadata(recorded)
        assert round_tripped is not None
        assert round_tripped[USAGE_METADATA_KEY] == fixture


@pytest.mark.parametrize(
    "fixture_name",
    ["usage.json"],
)
def test_metadata_fixture_is_present(fixture_name: str) -> None:
    """Cheap guard — surfaces a clear error if the schema fixtures move."""
    assert (REPO_ROOT / "schema" / "fixtures" / "metadata" / fixture_name).exists()
