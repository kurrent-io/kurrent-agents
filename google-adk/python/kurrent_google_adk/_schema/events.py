"""Thin re-export shim — forwards to the shared ``kurrent_agent_schema`` package.

This module used to vendor the canonical event models locally.  Now that the
``kurrent-agent-schema`` package exists it is a compatibility shim kept so
that the remaining service modules (``session_service``, ``memory_service``,
``artifact_service``, ``_codec``) can continue to import from here without
change until they are migrated off in Tasks 5-9.

Do not add new symbols here; add them to ``kurrent_agent_schema`` or to
``..events`` (ADK-specific types).
"""

from __future__ import annotations

from kurrent_agent_schema.events import (
    AgentConfig,
    ArtifactVersionCreated,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    EvalRunCompleted,
    EvalRunStarted,
    FactRetained,
    SessionEnded,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    ToolSpec,
    TurnScored,
    UserMessageReceived,
    _EventBase,
)
from kurrent_agent_schema.usage import TokenUsage

# ADK-specific types (not in the shared schema).
from ..events import (
    ADK_EXTENSION_KEY,
    AgentTransferred,
    Compaction,
    Rewind,
    StateDelta,
)

__all__ = [
    # ADK-specific
    "ADK_EXTENSION_KEY",
    "AgentConfig",
    "AgentTransferred",
    "ArtifactVersionCreated",
    "AssistantTextGenerated",
    "AssistantThinkingGenerated",
    "AssistantToolCallsGenerated",
    "Compaction",
    "EvalRunCompleted",
    "EvalRunStarted",
    "FactRetained",
    "Rewind",
    "SessionEnded",
    "SessionStarted",
    "StateDelta",
    "TokenUsage",
    "ToolCallInfo",
    "ToolResultReceived",
    "ToolSpec",
    "TurnScored",
    "UserMessageReceived",
    # Shared canonical types
    "_EventBase",
]

# Legacy alias kept for callers that do ``from ._schema.stream_names import ...``
# (no change needed; stream_names is a separate module).
