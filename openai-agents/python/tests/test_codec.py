"""Unit tests for the OpenAI session-item ↔ canonical codec."""

from __future__ import annotations

import json
from datetime import UTC, datetime

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
    import pytest
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


def test_serialize_stamps_schema_version() -> None:
    import json as _json
    from datetime import UTC, datetime as _dt
    from kurrent_agent_schema import UserMessageReceived
    from kurrent_openai_agents import _serialization

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
    from datetime import UTC, datetime as _dt
    from kurrent_openai_agents import _serialization
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
