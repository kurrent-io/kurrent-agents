"""ADK-specific event types and re-exports of the shared canonical schema.

Five event types live here because they describe ADK-specific session
mechanics (agent handoff, rewind boundary, event-range compaction,
session-scoped state delta, tool credential persistence) and are not
canonical across frameworks. The remaining canonical types are re-exported
from ``kurrent_agent_schema.events`` so internal callers have a single
import path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    TurnScored,
    UserMessageReceived,
    _EventBase,
)
from pydantic import Field

ADK_EXTENSION_KEY: str = "adk"
"""Slug used in ``extensions.<slug>`` for ADK-specific fields. Per
SCHEMA_v2 §5.3 each integration owns one slug; this is ours."""


# --- ADK-specific event types (registered in _serialization alongside canonical) ---


class AgentTransferred(_EventBase):
    """LLM-driven handoff within an ADK agent tree."""

    from_agent: str | None = None
    to_agent: str
    timestamp: datetime


class Rewind(_EventBase):
    """ADK rewind boundary; readers folding state must special-case."""

    rewind_before_invocation_id: str
    state_delta: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class Compaction(_EventBase):
    """Inline summary of an event range produced by ADK event compaction."""

    start_timestamp: datetime
    end_timestamp: datetime
    compacted_content: dict[str, Any]
    timestamp: datetime


class StateDelta(_EventBase):
    """Session-scoped state change (non-app, non-user keys)."""

    delta: dict[str, Any] = Field(default_factory=dict)
    invocation_id: str | None = None
    timestamp: datetime


class CredentialSaved(_EventBase):
    """ADK-specific event recording a tool OAuth credential.

    The ``credential`` field is a base64-encoded, cipher-self-describing
    wire blob. Format and AAD binding are defined in
    ``docs/superpowers/specs/2026-04-27-adk-credential-service-design.md``.
    """

    credential_key: str
    credential: str
    timestamp: datetime


__all__ = [  # noqa: RUF022
    "ADK_EXTENSION_KEY",
    # ADK-specific
    "AgentTransferred",
    "Rewind",
    "Compaction",
    "StateDelta",
    "CredentialSaved",
    # Re-exported canonical types
    "_EventBase",
    "AgentConfig",
    "ArtifactVersionCreated",
    "AssistantTextGenerated",
    "AssistantThinkingGenerated",
    "AssistantToolCallsGenerated",
    "EvalRunCompleted",
    "EvalRunStarted",
    "FactRetained",
    "InterruptIssued",
    "InterruptResolved",
    "SessionContinuedAs",
    "SessionEnded",
    "SessionStarted",
    "SubagentCompleted",
    "SubagentStarted",
    "ToolCallInfo",
    "ToolResultReceived",
    "TurnScored",
    "UserMessageReceived",
]
