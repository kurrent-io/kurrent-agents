"""Unit tests for the OpenAI session-item ↔ canonical codec."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from google.protobuf.json_format import MessageToDict
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    InterruptIssued,
    InterruptResolved,
    ToolResultReceived,
    UserMessageReceived,
)

from kurrent_openai_agents import _serialization
from kurrent_openai_agents._codec import canonical_to_items, items_to_canonical
from kurrent_openai_agents._openai_events import OPENAI_EXTENSION_KEY, OpenAIItem

TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _ext(event) -> dict:
    if OPENAI_EXTENSION_KEY not in event.extensions:
        return {}
    return MessageToDict(
        event.extensions[OPENAI_EXTENSION_KEY], preserving_proto_field_name=True
    )


def test_for_session_normalises_unsafe_chars() -> None:
    from kurrent_openai_agents._stream_names import for_session
    assert for_session("plain") == "AgentSession-plain"
    assert for_session("ses sion/01") == "AgentSession-ses%20sion%2F01"


def test_for_session_rejects_empty() -> None:
    from kurrent_openai_agents._stream_names import for_session
    with pytest.raises(ValueError):
        for_session("")


class TestCanonicalMapping:
    def test_user_text_message(self) -> None:
        items = [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert len(events) == 1
        assert isinstance(events[0], UserMessageReceived)
        assert events[0].content == "hello"
        assert events[0].message_index == 0
        # raw_item preserved verbatim under extensions.openai
        ext = _ext(events[0])
        assert ext["raw_item"] == items[0]
        assert ext["item_type"] == "message"

    def test_assistant_text_message(self) -> None:
        items = [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi there"}],
        }]
        events = items_to_canonical(items, start_index=5, timestamp=TS)
        assert isinstance(events[0], AssistantTextGenerated)
        assert events[0].content == "hi there"
        assert events[0].message_index == 5

    def test_function_call(self) -> None:
        items = [{
            "type": "function_call",
            "call_id": "c1",
            "name": "search",
            "arguments": '{"q": "kurrent"}',
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], AssistantToolCallsGenerated)
        tc = events[0].tool_calls[0]
        assert tc.call_id == "c1"
        assert tc.tool_name == "search"
        assert MessageToDict(tc.arguments, preserving_proto_field_name=True) == {"q": "kurrent"}

    def test_function_call_empty_arguments(self) -> None:
        """Empty-dict args must round-trip as ``{}`` (not collapse to None) — schema commit ff1540d."""
        items = [{
            "type": "function_call",
            "call_id": "c1",
            "name": "list_notes",
            "arguments": "{}",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        tc = events[0].tool_calls[0]
        assert tc.HasField("arguments")
        assert MessageToDict(tc.arguments, preserving_proto_field_name=True) == {}

    def test_function_call_output(self) -> None:
        items = [{
            "type": "function_call_output",
            "call_id": "c1",
            "output": "found 3 hits",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], ToolResultReceived)
        assert events[0].call_id == "c1"
        assert events[0].result == "found 3 hits"


class TestNonCanonicalItems:
    def test_handoff_call_stays_as_openai_item(self) -> None:
        items = [{
            "type": "handoff_call",
            "call_id": "h1",
            "name": "handoff_to_expert",
            "arguments": "{}",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], OpenAIItem)
        assert events[0].item_type == "handoff_call"
        assert events[0].raw_item == items[0]

    def test_unknown_type_stays_as_openai_item(self) -> None:
        items = [{"type": "shell_call", "call_id": "s1", "command": "ls"}]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], OpenAIItem)
        assert events[0].item_type == "shell_call"


class TestRoundTrip:
    def test_simple_conversation_round_trip(self) -> None:
        items = [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "hello"}]},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "hi"}]},
            {"type": "function_call", "call_id": "c1", "name": "search",
             "arguments": '{"q": "x"}'},
            {"type": "function_call_output", "call_id": "c1", "output": '{"hits": 3}'},
        ]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        restored = canonical_to_items(events)
        assert restored == items

    def test_cross_framework_fallback(self) -> None:
        """When no raw_item extension is present, we rebuild a minimal item."""
        evt = UserMessageReceived(message_index=0)
        evt.content = "hello"
        evt.timestamp.FromDatetime(TS.replace(tzinfo=None))
        items = canonical_to_items([evt])
        assert items == [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }]


class TestTimestampNormalisation:
    """Naive timestamps must be interpreted as UTC, not silently as local time.

    On Python 3.11+, ``naive.astimezone(UTC)`` does not raise — it interprets
    the input as local time and converts. That silently produces a wrong
    canonical UTC for any caller passing a naive datetime, regardless of
    intent. The codec normalises tz-naive inputs to UTC at the entry point.
    """

    def _proto_ts_to_aware(self, evt) -> datetime:
        """Read a proto Timestamp back as a tz-aware UTC datetime."""
        return evt.timestamp.ToDatetime(tzinfo=UTC)

    def test_naive_timestamp_treated_as_utc_not_local(self) -> None:
        wall = datetime(2026, 4, 19, 12, 0)  # naive
        utc = wall.replace(tzinfo=UTC)

        items = [{"type": "message", "role": "user",
                  "content": [{"type": "input_text", "text": "hi"}]}]
        from_naive = items_to_canonical(items, start_index=0, timestamp=wall)
        from_aware = items_to_canonical(items, start_index=0, timestamp=utc)

        assert (
            self._proto_ts_to_aware(from_naive[0])
            == self._proto_ts_to_aware(from_aware[0])
            == utc
        ), "naive timestamp must round-trip as UTC, not as local-time-converted-to-UTC"

    def test_aware_non_utc_timestamp_converted_to_utc(self) -> None:
        from datetime import timedelta, timezone

        plus_two = timezone(timedelta(hours=2))
        local = datetime(2026, 4, 19, 14, 0, tzinfo=plus_two)
        utc = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

        items = [{"type": "message", "role": "user",
                  "content": [{"type": "input_text", "text": "hi"}]}]
        events = items_to_canonical(items, start_index=0, timestamp=local)

        assert self._proto_ts_to_aware(events[0]) == utc


def test_serialize_stamps_schema_version() -> None:
    import json as _json
    from datetime import UTC
    from datetime import datetime as _dt

    from kurrent_agent_schema import UserMessageReceived

    event = UserMessageReceived(message_index=0)
    event.timestamp.FromDatetime(_dt(2026, 5, 5, tzinfo=UTC))
    event.content = "hi"

    new_event = _serialization.serialize(event)

    assert new_event.type == "UserMessageReceived"
    payload = _json.loads(new_event.data)
    assert payload["content"] == "hi"
    metadata = _json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2


def test_serialize_pydantic_openai_item() -> None:
    import json as _json
    from datetime import UTC
    from datetime import datetime as _dt

    from kurrent_openai_agents._openai_events import OpenAIItem

    item = OpenAIItem(
        item_type="computer_call",
        raw_item={"type": "computer_call", "id": "x"},
        message_index=3,
        timestamp=_dt(2026, 5, 5, tzinfo=UTC),
    )
    new_event = _serialization.serialize(item)
    assert new_event.type == "OpenAIItem"
    payload = _json.loads(new_event.data)
    assert payload["item_type"] == "computer_call"
    assert payload["raw_item"] == {"type": "computer_call", "id": "x"}


class TestDeserializeTolerance:
    """One bad event must not block the whole session — see Qodo review on PR #54."""

    def _record(self, *, type: str, data: bytes) -> Any:
        from uuid import uuid4

        from kurrentdbclient import RecordedEvent

        return RecordedEvent(
            type=type,
            data=data,
            metadata=b"",
            content_type="application/json",
            id=uuid4(),
            stream_name="AgentSession-test",
            stream_position=0,
            commit_position=0,
            prepare_position=0,
        )

    def test_invalid_json_for_known_canonical_type_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorded = self._record(type="UserMessageReceived", data=b"{not json")
        with caplog.at_level("WARNING", logger="kurrent_openai_agents._serialization"):
            assert _serialization.deserialize(recorded) is None
        assert "UserMessageReceived" in caplog.text

    def test_invalid_utf8_for_known_canonical_type_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorded = self._record(type="UserMessageReceived", data=b"\xff\xfe\x00bad")
        with caplog.at_level("WARNING", logger="kurrent_openai_agents._serialization"):
            assert _serialization.deserialize(recorded) is None
        assert "UserMessageReceived" in caplog.text

    def test_invalid_json_for_known_pydantic_type_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorded = self._record(type="OpenAIItem", data=b"{not json")
        with caplog.at_level("WARNING", logger="kurrent_openai_agents._serialization"):
            assert _serialization.deserialize(recorded) is None
        assert "OpenAIItem" in caplog.text

    def test_schema_violation_for_known_pydantic_type_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Valid JSON but missing required fields (item_type, raw_item, …).
        recorded = self._record(type="OpenAIItem", data=b'{"unrelated": 1}')
        with caplog.at_level("WARNING", logger="kurrent_openai_agents._serialization"):
            assert _serialization.deserialize(recorded) is None
        assert "OpenAIItem" in caplog.text

    def test_unknown_event_type_still_returns_none_silently(self) -> None:
        # Pre-existing forward-compat behaviour — no warning should be logged
        # because we don't know enough to call this "malformed".
        recorded = self._record(type="SomeFutureEvent", data=b'{"x": 1}')
        assert _serialization.deserialize(recorded) is None


class TestReasoningMapping:
    def test_plaintext_reasoning_maps_to_thinking(self) -> None:
        items = [{
            "type": "reasoning",
            "id": "r1",
            "content": [{"type": "reasoning_text", "text": "because..."}],
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert len(events) == 1
        assert isinstance(events[0], AssistantThinkingGenerated)
        assert events[0].content == "because..."
        # Plaintext path must omit `encrypted` from the wire; under Edition 2024
        # this means *not* setting the field rather than setting it to False.
        assert not events[0].HasField("encrypted")
        assert not events[0].HasField("signature")
        assert _ext(events[0])["raw_item"] == items[0]

    def test_encrypted_reasoning_maps_to_thinking(self) -> None:
        items = [{
            "type": "reasoning",
            "id": "r2",
            "encrypted_content": "AAA-OPAQUE-BLOB-AAA",
            "signature": "sig-deadbeef",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], AssistantThinkingGenerated)
        assert events[0].encrypted is True
        assert events[0].signature == "sig-deadbeef"
        assert not events[0].HasField("content")
        ext = _ext(events[0])
        assert ext["thinking"]["raw"] == "AAA-OPAQUE-BLOB-AAA"
        assert ext["raw_item"] == items[0]

    def test_reasoning_round_trips_through_raw_item(self) -> None:
        items = [{
            "type": "reasoning",
            "id": "r1",
            "content": [{"type": "reasoning_text", "text": "thinking..."}],
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert canonical_to_items(events) == items


class TestMcpApprovalMapping:
    def test_mcp_approval_request_maps_to_interrupt_issued(self) -> None:
        items = [{
            "type": "mcp_approval_request",
            "id": "req-1",
            "name": "publish_post",
            "arguments": '{"title": "hi"}',
            "server_label": "blog-mcp",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], InterruptIssued)
        assert events[0].request_id == "req-1"
        assert events[0].kind == "approval"
        assert events[0].tool_name == "publish_post"
        ext = _ext(events[0])
        assert ext["interrupt"]["proposed_call"] == {
            "id": "req-1",
            "name": "publish_post",
            "arguments": {"title": "hi"},
        }
        assert ext["raw_item"] == items[0]

    def test_mcp_approval_response_allow_maps_to_resolved(self) -> None:
        items = [{
            "type": "mcp_approval_response",
            "approval_request_id": "req-1",
            "approve": True,
            "reason": "looks fine",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], InterruptResolved)
        assert events[0].request_id == "req-1"
        assert events[0].outcome == "allow"
        assert events[0].response == "looks fine"

    def test_mcp_approval_response_deny_maps_to_resolved(self) -> None:
        items = [{
            "type": "mcp_approval_response",
            "approval_request_id": "req-2",
            "approve": False,
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], InterruptResolved)
        assert events[0].outcome == "deny"
        assert not events[0].HasField("response")

    def test_mcp_round_trip(self) -> None:
        items = [
            {
                "type": "mcp_approval_request",
                "id": "req-1",
                "name": "publish_post",
                "arguments": '{"title": "hi"}',
                "server_label": "blog-mcp",
            },
            {
                "type": "mcp_approval_response",
                "approval_request_id": "req-1",
                "approve": True,
            },
        ]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        restored = canonical_to_items(events)
        assert restored == items
