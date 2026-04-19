"""Wire-format tests.

These lock in the JSON shape of every event so the Python and C# implementations
stay mutually intelligible. If a C# event record changes, the equivalent Pydantic
model and this test must change together.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kurrent_agent_framework import events, serialization


def _roundtrip_bytes(event: events._EventBase) -> dict:
    new_event = serialization.serialize(event)
    return json.loads(new_event.data)


def test_session_started_wire_format() -> None:
    evt = events.SessionStarted(
        agent_name="demo",
        model="gpt-4o",
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _roundtrip_bytes(evt)
    assert payload == {
        "agent_name": "demo",
        "model": "gpt-4o",
        "timestamp": "2026-04-13T12:00:00Z",
    }


def test_user_message_wire_format() -> None:
    evt = events.UserMessageReceived(
        content="hello",
        message_id="m1",
        message_index=0,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _roundtrip_bytes(evt)
    assert payload["content"] == "hello"
    assert payload["message_id"] == "m1"
    assert payload["message_index"] == 0
    assert "author_name" not in payload  # exclude_none


def test_assistant_tool_calls_wire_format() -> None:
    evt = events.AssistantToolCallsGenerated(
        tool_calls=[
            events.ToolCallInfo(
                call_id="c1",
                tool_name="get_weather",
                arguments={"city": "London"},
            )
        ],
        message_index=1,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _roundtrip_bytes(evt)
    assert payload["tool_calls"] == [
        {"call_id": "c1", "tool_name": "get_weather", "arguments": {"city": "London"}}
    ]
    assert payload["message_index"] == 1


def test_turn_scored_wire_format() -> None:
    evt = events.TurnScored(
        session_id="s1",
        turn_index=0,
        input="What's the weather?",
        output="Sunny.",
        score=0.9,
        score_label="good",
        reason="used tool",
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _roundtrip_bytes(evt)
    assert payload == {
        "session_id": "s1",
        "turn_index": 0,
        "input": "What's the weather?",
        "output": "Sunny.",
        "score": 0.9,
        "score_label": "good",
        "reason": "used tool",
        "timestamp": "2026-04-13T12:00:00Z",
    }


def test_fact_retained_wire_format() -> None:
    """Matches the C# KurrentDBAgentMemory inline shape: {fact, retained_at}."""
    evt = events.FactRetained(
        fact="user prefers concise answers",
        retained_at=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _roundtrip_bytes(evt)
    assert payload == {
        "fact": "user prefers concise answers",
        "retained_at": "2026-04-13T12:00:00Z",
    }


def test_event_type_names_match_csharp() -> None:
    """The CLR-name strings on the wire must match the C# EventTypeMap exactly."""
    expected = {
        events.SessionStarted:              "SessionStarted",
        events.SessionEnded:                "SessionEnded",
        events.UserMessageReceived:         "UserMessageReceived",
        events.AssistantTextGenerated:      "AssistantTextGenerated",
        events.AssistantToolCallsGenerated: "AssistantToolCallsGenerated",
        events.ToolResultReceived:          "ToolResultReceived",
        events.FactRetained:                "FactRetained",
        events.TokenUsageRecorded:          "TokenUsageRecorded",
        events.EvalRunStarted:              "EvalRunStarted",
        events.TurnScored:                  "TurnScored",
        events.EvalRunCompleted:            "EvalRunCompleted",
    }
    for cls, expected_name in expected.items():
        assert serialization._TYPE_TO_NAME[cls] == expected_name
        assert serialization._NAME_TO_TYPE[expected_name] is cls
