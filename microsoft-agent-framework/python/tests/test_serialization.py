"""Wire-format tests for the MAF Python serializer adapter.

Pins the on-the-wire JSON shape of events emitted via :mod:`kurrent_agent_framework.serialization`
and verifies ``$schema_version`` metadata stamping per SCHEMA_v2 §9. Drift-detection
against the shared canonical fixtures lives in ``test_fixtures_round_trip.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kurrent_agent_schema import (
    AssistantToolCallsGenerated,
    FactRetained,
    SessionStarted,
    ToolCallInfo,
    TurnScored,
    UserMessageReceived,
)
from kurrent_agent_schema.events import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES, _EventBase

from kurrent_agent_framework import serialization


def _payload(event: _EventBase) -> dict:
    new_event = serialization.serialize(event)
    return json.loads(new_event.data)


def _metadata(event: _EventBase) -> dict:
    new_event = serialization.serialize(event)
    return json.loads(new_event.metadata)


def test_session_started_wire_format() -> None:
    evt = SessionStarted(
        agent_name="demo",
        model="gpt-4o",
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    assert _payload(evt) == {
        "agent_name": "demo",
        "model": "gpt-4o",
        "timestamp": "2026-04-13T12:00:00Z",
    }


def test_user_message_wire_format() -> None:
    evt = UserMessageReceived(
        content="hello",
        message_id="m1",
        message_index=0,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _payload(evt)
    assert payload["content"] == "hello"
    assert payload["message_id"] == "m1"
    assert payload["message_index"] == 0
    assert "author_name" not in payload  # exclude_none


def test_assistant_tool_calls_wire_format() -> None:
    evt = AssistantToolCallsGenerated(
        tool_calls=[
            ToolCallInfo(
                call_id="c1",
                tool_name="get_weather",
                arguments={"city": "London"},
            )
        ],
        message_index=1,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    payload = _payload(evt)
    assert payload["tool_calls"] == [
        {"call_id": "c1", "tool_name": "get_weather", "arguments": {"city": "London"}}
    ]
    assert payload["message_index"] == 1


def test_turn_scored_wire_format() -> None:
    evt = TurnScored(
        session_id="s1",
        turn_index=0,
        input="What's the weather?",
        output="Sunny.",
        score=0.9,
        score_label="good",
        reason="used tool",
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    assert _payload(evt) == {
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
    evt = FactRetained(
        fact="user prefers concise answers",
        retained_at=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    assert _payload(evt) == {
        "fact": "user prefers concise answers",
        "retained_at": "2026-04-13T12:00:00Z",
    }


def test_event_type_name_registry_round_trips() -> None:
    """Sanity check: every registered canonical type resolves in both directions."""
    for cls, name in EVENT_TYPE_NAMES.items():
        assert EVENT_TYPE_BY_NAME[name] is cls


def test_schema_version_is_stamped_on_metadata() -> None:
    """Writers must stamp ``$schema_version = 2`` on every canonical event
    (SCHEMA_v2 §9). The writer's version always wins over caller-supplied
    metadata so the wire version stays authoritative."""
    evt = UserMessageReceived(
        content="hi",
        message_index=0,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    assert _metadata(evt) == {"$schema_version": 2}


def test_caller_metadata_is_preserved_alongside_schema_version() -> None:
    evt = UserMessageReceived(
        content="hi",
        message_index=0,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    new_event = serialization.serialize(evt, metadata={"request_id": "r1"})
    meta = json.loads(new_event.metadata)
    assert meta == {"request_id": "r1", "$schema_version": 2}


def test_caller_cannot_override_schema_version() -> None:
    """A caller supplying ``$schema_version`` in metadata cannot forge the
    wire version — the writer's value always wins (stamped last)."""
    evt = UserMessageReceived(
        content="hi",
        message_index=0,
        timestamp=datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    )
    new_event = serialization.serialize(evt, metadata={"$schema_version": 99})
    meta = json.loads(new_event.metadata)
    assert meta["$schema_version"] == 2
