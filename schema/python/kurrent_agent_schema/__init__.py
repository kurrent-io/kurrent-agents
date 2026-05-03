"""Canonical event schema for Kurrent agent integrations.

See ``schema/SCHEMA_v2.md`` at the repo root for the prose specification.
"""

from kurrent_agent_schema._generated.kurrent.agent.v2.events_pb2 import (
    ArtifactVersionCreated,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    EvalRunCompleted,
    EvalRunStarted,
    FactRetained,
    InterruptIssued,
    InterruptResolved,
    SessionContinuedAs,
    SessionEnded,
    SessionScored,
    SessionStarted,
    SubagentCompleted,
    SubagentStarted,
    ToolResultReceived,
    TurnScored,
    UserMessageReceived,
)
from kurrent_agent_schema._generated.kurrent.agent.v2.usage_pb2 import TokenUsage
from kurrent_agent_schema._generated.kurrent.agent.v2.value_types_pb2 import (
    AgentConfig,
    ToolCallInfo,
    ToolSpec,
)
from kurrent_agent_schema.json import from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES
from kurrent_agent_schema.streams import (
    agent_artifact_stream,
    agent_memory_stream,
    agent_session_stream,
    agent_subsession_stream,
    eval_run_stream,
)
from kurrent_agent_schema.usage import USAGE_METADATA_KEY
from kurrent_agent_schema.version import SCHEMA_VERSION

__all__ = [
    # Value types
    "AgentConfig",
    "ToolSpec",
    "ToolCallInfo",
    "TokenUsage",
    # Session lifecycle
    "SessionStarted",
    "SessionEnded",
    "SessionContinuedAs",
    # Conversation
    "UserMessageReceived",
    "AssistantTextGenerated",
    "AssistantToolCallsGenerated",
    "AssistantThinkingGenerated",
    "ToolResultReceived",
    # Interrupts
    "InterruptIssued",
    "InterruptResolved",
    # Subagents
    "SubagentStarted",
    "SubagentCompleted",
    # Memory / artifacts / eval
    "FactRetained",
    "ArtifactVersionCreated",
    "EvalRunStarted",
    "TurnScored",
    "SessionScored",
    "EvalRunCompleted",
    # Stream builders
    "agent_session_stream",
    "agent_subsession_stream",
    "agent_memory_stream",
    "agent_artifact_stream",
    "eval_run_stream",
    # Helpers
    "to_json",
    "from_json",
    # Registry
    "EVENT_TYPE_NAMES",
    "EVENT_TYPE_BY_NAME",
    # Constants
    "USAGE_METADATA_KEY",
    "SCHEMA_VERSION",
]
