"""Canonical event models (schema v2).

See ``schema/SCHEMA_v2.md`` at the repo root for the prose specification.

All events inherit from ``_EventBase``, which carries the ``extensions``
envelope and the shared Pydantic configuration (``extra="ignore"`` for
forward-compatibility, frozen for hashability, snake_case JSON).

Stream placement and semantic conventions live in ``streams.py`` and the
schema doc — this module is strictly the type shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _EventBase(BaseModel):
    """Shared configuration for every canonical event.

    ``extra="ignore"`` preserves forward-compatibility: unknown fields added
    in a later minor version are dropped on read, not raised.

    ``ser_json_bytes="base64"`` / ``val_json_bytes="base64"`` round-trip
    ``bytes`` fields through JSON for ``ArtifactVersionCreated.inline_bytes``.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    extensions: dict[str, dict[str, Any]] | None = None
    """Framework-specific extension envelope keyed by slug (``adk``, ``afw``,
    ``strands``, ``openai``, ``claude_sdk``, ``claude_code``, …).
    See ``schema/SCHEMA_v2.md §5``."""


# --- Value types -------------------------------------------------------------


class ToolSpec(BaseModel):
    """Tool description captured in ``AgentConfig.tools``."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    source: str | None = None


class AgentConfig(BaseModel):
    """Informational snapshot of the agent configuration at session start.

    All fields optional. Not a contract — readers use this for context, not
    to reproduce the writer's agent.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    tools: list[ToolSpec] | None = None
    plugins: list[str] | None = None
    conversation_manager: dict[str, Any] | None = None
    model_parameters: dict[str, Any] | None = None


class ToolCallInfo(BaseModel):
    """One tool call within ``AssistantToolCallsGenerated.tool_calls``."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    call_id: str
    tool_name: str
    arguments: dict[str, Any] | None = None


# --- Session lifecycle (SCHEMA_v2.md §3.1) -----------------------------------


class SessionStarted(_EventBase):
    app_name: str | None = None
    agent_name: str | None = None
    model: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    agent_config: AgentConfig | None = None
    previous_session_id: str | None = None
    """Prior session this one resumes or forks from. New in v2."""
    timestamp: datetime


class SessionEnded(_EventBase):
    reason: str | None = None
    timestamp: datetime


class SessionContinuedAs(_EventBase):
    """Written to the predecessor session pointing forward to its successor.

    Paired with ``SessionStarted.previous_session_id`` on the successor, this
    forms a bidirectional chain. New in v2.
    """

    next_session_id: str
    reason: str | None = None
    timestamp: datetime


# --- Conversation events (SCHEMA_v2.md §3.4) ---------------------------------


class UserMessageReceived(_EventBase):
    content: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    created_at: datetime | None = None
    message_index: int
    timestamp: datetime


class AssistantTextGenerated(_EventBase):
    content: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    created_at: datetime | None = None
    message_index: int
    timestamp: datetime


class AssistantToolCallsGenerated(_EventBase):
    tool_calls: list[ToolCallInfo]
    content: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    created_at: datetime | None = None
    message_index: int
    timestamp: datetime


class AssistantThinkingGenerated(_EventBase):
    """Assistant reasoning output (extended thinking / o-series / Gemini thinking).

    Plaintext reasoning in ``content`` for Claude and Gemini; encrypted blobs
    from OpenAI o-series have ``encrypted=True`` with the opaque value in
    ``extensions.openai.thinking.raw`` and the provider signature on
    ``signature``. New in v2 (SCHEMA_v2.md §3.2).
    """

    content: str | None = None
    encrypted: bool = False
    signature: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    created_at: datetime | None = None
    message_index: int
    timestamp: datetime


class ToolResultReceived(_EventBase):
    call_id: str
    tool_name: str | None = None
    result: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    created_at: datetime | None = None
    message_index: int
    timestamp: datetime


# --- Interrupts (SCHEMA_v2.md §3.3) ------------------------------------------


class InterruptIssued(_EventBase):
    """Mid-turn human-in-the-loop pause (permission prompt, approval, input, auth).

    ``kind`` is an open string; the documented set is
    ``permission | approval | input | auth``, readers must tolerate unknowns.
    Framework-specific details (tool_input, auth challenge) go in
    ``extensions.{framework}.interrupt``. New in v2.
    """

    request_id: str
    kind: str
    tool_name: str | None = None
    prompt: str | None = None
    timestamp: datetime


class InterruptResolved(_EventBase):
    """Resolution of an ``InterruptIssued``, keyed by the same ``request_id``.

    ``outcome`` documented set: ``allow | allow_once | allow_always | deny |
    cancel | answered | timeout``. Open string; readers tolerate unknowns.
    New in v2.
    """

    request_id: str
    outcome: str
    response: str | None = None
    timestamp: datetime


# --- Subagents (SCHEMA_v2.md §3.5) -------------------------------------------
# Written to the PARENT session stream to record subagent lifecycle; the
# subagent's own conversation lives in AgentSubsession-{parent}-{agent_id}.


class SubagentStarted(_EventBase):
    agent_id: str
    agent_type: str | None = None
    prompt: str | None = None
    subsession_stream: str | None = None
    timestamp: datetime


class SubagentCompleted(_EventBase):
    agent_id: str
    outcome: str | None = None
    summary: str | None = None
    timestamp: datetime


# --- Memory (SCHEMA_v2.md §3.7) ----------------------------------------------


class FactRetained(_EventBase):
    fact: str
    retained_at: datetime


# --- Artifacts (SCHEMA_v2.md §3.7) -------------------------------------------


class ArtifactVersionCreated(_EventBase):
    version: int
    mime_type: str | None = None
    inline_bytes: bytes | None = None
    canonical_uri: str | None = None
    custom_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- Evaluation (SCHEMA_v2.md §3.7) ------------------------------------------


class EvalRunStarted(_EventBase):
    session_id: str
    scorer: str
    criteria: str
    timestamp: datetime


class TurnScored(_EventBase):
    session_id: str
    turn_index: int
    input: str | None = None
    output: str | None = None
    score: float
    score_label: str | None = None
    reason: str | None = None
    timestamp: datetime


class EvalRunCompleted(_EventBase):
    session_id: str
    turns_scored: int
    average_score: float
    total_cost: float | None = None
    timestamp: datetime


# --- Event type name registry -------------------------------------------------

EVENT_TYPE_NAMES: dict[type[_EventBase], str] = {
    SessionStarted: "SessionStarted",
    SessionEnded: "SessionEnded",
    SessionContinuedAs: "SessionContinuedAs",
    UserMessageReceived: "UserMessageReceived",
    AssistantTextGenerated: "AssistantTextGenerated",
    AssistantToolCallsGenerated: "AssistantToolCallsGenerated",
    AssistantThinkingGenerated: "AssistantThinkingGenerated",
    ToolResultReceived: "ToolResultReceived",
    InterruptIssued: "InterruptIssued",
    InterruptResolved: "InterruptResolved",
    SubagentStarted: "SubagentStarted",
    SubagentCompleted: "SubagentCompleted",
    FactRetained: "FactRetained",
    ArtifactVersionCreated: "ArtifactVersionCreated",
    EvalRunStarted: "EvalRunStarted",
    TurnScored: "TurnScored",
    EvalRunCompleted: "EvalRunCompleted",
}
"""Canonical event type name on the wire (KurrentDB ``event_type``)
for each model class. Integration writers use this to stamp events;
readers use the inverse lookup."""

EVENT_TYPE_BY_NAME: dict[str, type[_EventBase]] = {v: k for k, v in EVENT_TYPE_NAMES.items()}
