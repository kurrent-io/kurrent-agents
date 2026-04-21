"""Canonical event schema for Kurrent agent integrations.

See ``schema/SCHEMA_v2.md`` at the repo root for the prose specification.
This package is the Python mirror; ``Kurrent.Agent.Schema`` is the .NET mirror.
"""

from kurrent_agent_schema.events import (
    AgentConfig,
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
    SessionStarted,
    SubagentCompleted,
    SubagentStarted,
    ToolCallInfo,
    ToolResultReceived,
    ToolSpec,
    TurnScored,
    UserMessageReceived,
)
from kurrent_agent_schema.streams import (
    agent_artifact_stream,
    agent_memory_stream,
    agent_session_stream,
    agent_subsession_stream,
    eval_run_stream,
)
from kurrent_agent_schema.usage import TokenUsage, USAGE_METADATA_KEY
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
    # Memory
    "FactRetained",
    # Artifacts
    "ArtifactVersionCreated",
    # Evaluation
    "EvalRunStarted",
    "TurnScored",
    "EvalRunCompleted",
    # Stream builders
    "agent_session_stream",
    "agent_subsession_stream",
    "agent_memory_stream",
    "agent_artifact_stream",
    "eval_run_stream",
    # Constants
    "USAGE_METADATA_KEY",
    "SCHEMA_VERSION",
]
