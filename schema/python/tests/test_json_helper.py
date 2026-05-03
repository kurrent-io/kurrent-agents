from kurrent_agent_schema._generated.kurrent.agent.v2 import events_pb2
from kurrent_agent_schema.json import to_json, from_json


def test_to_json_uses_snake_case() -> None:
    msg = events_pb2.SessionStarted(app_name="my-app")
    js = to_json(msg)
    assert '"app_name"' in js
    assert '"appName"' not in js


def test_from_json_round_trip() -> None:
    src = '{"app_name": "x", "timestamp": "2026-01-01T00:00:00Z"}'
    msg = from_json(events_pb2.SessionStarted, src)
    assert msg.app_name == "x"


from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME


def test_registry_contains_all_canonical_events() -> None:
    expected = {
        "SessionStarted", "SessionEnded", "SessionContinuedAs",
        "UserMessageReceived", "AssistantTextGenerated",
        "AssistantToolCallsGenerated", "AssistantThinkingGenerated",
        "ToolResultReceived", "InterruptIssued", "InterruptResolved",
        "SubagentStarted", "SubagentCompleted", "FactRetained",
        "ArtifactVersionCreated", "EvalRunStarted", "TurnScored",
        "SessionScored", "EvalRunCompleted",
    }
    assert set(EVENT_TYPE_BY_NAME) == expected
