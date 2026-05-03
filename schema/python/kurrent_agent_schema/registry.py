"""Event-type-name ↔ generated message class registry.

Stamps the KurrentDB ``event_type`` for each canonical event class.
"""

from __future__ import annotations

from google.protobuf.message import Message

from kurrent_agent_schema._generated.kurrent.agent.v2 import events_pb2 as _ev

EVENT_TYPE_NAMES: dict[type[Message], str] = {
    _ev.SessionStarted: "SessionStarted",
    _ev.SessionEnded: "SessionEnded",
    _ev.SessionContinuedAs: "SessionContinuedAs",
    _ev.UserMessageReceived: "UserMessageReceived",
    _ev.AssistantTextGenerated: "AssistantTextGenerated",
    _ev.AssistantToolCallsGenerated: "AssistantToolCallsGenerated",
    _ev.AssistantThinkingGenerated: "AssistantThinkingGenerated",
    _ev.ToolResultReceived: "ToolResultReceived",
    _ev.InterruptIssued: "InterruptIssued",
    _ev.InterruptResolved: "InterruptResolved",
    _ev.SubagentStarted: "SubagentStarted",
    _ev.SubagentCompleted: "SubagentCompleted",
    _ev.FactRetained: "FactRetained",
    _ev.ArtifactVersionCreated: "ArtifactVersionCreated",
    _ev.EvalRunStarted: "EvalRunStarted",
    _ev.TurnScored: "TurnScored",
    _ev.SessionScored: "SessionScored",
    _ev.EvalRunCompleted: "EvalRunCompleted",
}

EVENT_TYPE_BY_NAME: dict[str, type[Message]] = {v: k for k, v in EVENT_TYPE_NAMES.items()}
