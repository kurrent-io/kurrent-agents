"""Canonical agent event schema — Pydantic v2 models.

These models are the Python mirror of the canonical schema defined in
``schema/SCHEMA.md`` at the repo root. They are kept here (vendored) until a
shared ``kurrent-agent-schema`` package is extracted; at that point this module
becomes a thin re-export.

The schema is snake_case JSON, shared wire-compatibly with the Microsoft Agent
Framework integrations (.NET and Python). Every canonical event carries an
optional ``extensions`` envelope keyed by framework slug (``"adk"``, ``"afw"``,
``"strands"``, …) — non-canonical state rides there without breaking cross-
framework reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Framework slugs used for the extension envelope on canonical events.
# Each integration emits under its own key; readers of other frameworks'
# streams must tolerate (and pass through) keys they don't own.
ADK_EXTENSION_KEY = "adk"
STRANDS_EXTENSION_KEY = "strands"
AFW_EXTENSION_KEY = "afw"
OPENAI_EXTENSION_KEY = "openai"
CLAUDE_SDK_EXTENSION_KEY = "claude_sdk"


class _EventBase(BaseModel):
    """Shared config for every canonical event model.

    ``extra="ignore"`` ensures forward-compatibility: fields added in a future
    schema revision are silently dropped on read rather than raising, so older
    readers keep working against newer streams.

    ``ser_json_bytes`` / ``val_json_bytes`` round-trip ``bytes`` fields as
    base64 strings in JSON — required for binary payloads on
    ``ArtifactVersionCreated.inline_bytes``. Matches how ADK's ``Event``
    model handles the same shape.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    extensions: dict[str, dict[str, Any]] | None = None
    """Framework-specific extension envelope. See ``SCHEMA.md`` §2.1."""


# --- Session lifecycle (SCHEMA.md §3.1) --------------------------------------


class ToolSpec(BaseModel):
    """Tool description captured in ``AgentConfig.tools``."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    source: str | None = None  # e.g. "mcp", "vended", "custom"


class AgentConfig(BaseModel):
    """Optional, informational snapshot of the agent's configuration.

    Populated on ``SessionStarted``; all fields optional. A cross-framework
    reader uses this for context, not as a contract to reproduce.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    tools: list[ToolSpec] | None = None
    plugins: list[str] | None = None
    conversation_manager: dict[str, Any] | None = None
    model_parameters: dict[str, Any] | None = None


class SessionStarted(_EventBase):
    app_name: str | None = None
    agent_name: str | None = None
    model: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    agent_config: AgentConfig | None = None
    timestamp: datetime


class SessionEnded(_EventBase):
    reason: str | None = None
    timestamp: datetime


# --- Conversation events (SCHEMA.md §3.2) ------------------------------------


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


class ToolCallInfo(BaseModel):
    """One tool call within an ``AssistantToolCallsGenerated`` event."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    call_id: str
    tool_name: str
    arguments: dict[str, Any] | None = None


class AssistantToolCallsGenerated(_EventBase):
    tool_calls: list[ToolCallInfo]
    content: str | None = None
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


# --- Usage metadata (SCHEMA.md §3.4) -----------------------------------------
# Usage rides as KurrentDB event metadata under the "$usage" key on assistant
# events, *not* as a standalone event. This model parses/produces that metadata.


class TokenUsage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None


# --- Memory (SCHEMA.md §3.6) -------------------------------------------------


class FactRetained(_EventBase):
    fact: str
    retained_at: datetime


# --- Artifacts (SCHEMA.md §3.7) ----------------------------------------------


class ArtifactVersionCreated(_EventBase):
    version: int
    mime_type: str | None = None
    inline_bytes: bytes | None = None
    canonical_uri: str | None = None
    custom_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- Evaluation (SCHEMA.md §3.5) ---------------------------------------------


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


# --- ADK-specific event types in the session stream (SCHEMA.md §4) -----------
# These share ``AgentSession-{session_id}`` with canonical events. Non-ADK
# readers ignore them.


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


# --- Strands-specific event types in the session stream (DESIGN.md §5) -------
# These share ``AgentSession-{session_id}`` with canonical events. Non-Strands
# readers ignore them.


class StrandsAgentState(_EventBase):
    """Serialized Strands ``SessionAgent`` — emitted on every ``sync_agent``.

    Carries user-managed state, conversation-manager state, and Strands'
    internal state (interrupt + model state). Readers take the latest.
    """

    agent_id: str
    state: dict[str, Any] = Field(default_factory=dict)
    conversation_manager_state: dict[str, Any] = Field(default_factory=dict)
    internal_state: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class MessageRedacted(_EventBase):
    """Marks a prior message as redacted by a guardrail.

    ``redact_message`` carries the replacement content; readers replace the
    original message at the indicated invocation / message index.
    """

    message_index: int
    redact_message: dict[str, Any]
    timestamp: datetime


# --- OpenAI-Agents-specific event types in the session stream ----------------
# Used for OpenAI Agents SDK items that don't map onto canonical conversation
# events (reasoning, handoff_*, mcp_approval_*, computer_call, shell_call,
# etc.). Stores the raw ``ResponseInputItemParam`` dict verbatim so the SDK
# can reconstruct its session history losslessly on ``get_items``.


class OpenAIItem(_EventBase):
    """Wraps a non-canonical OpenAI Agents SDK session item verbatim."""

    item_type: str
    raw_item: dict[str, Any]
    message_index: int
    timestamp: datetime


# --- Claude-Agent-SDK-specific event types in the session stream -------------
# The Claude Agent SDK wraps a subprocess (the Claude Code CLI) which owns
# its own JSONL transcript format. Per the SDK docs, entries are "pass-through
# blobs; round-tripping json.dumps / json.loads is the only required
# invariant" — so v0 preserves them verbatim as one ``ClaudeSDKEntry`` event
# per JSONL line. A follow-up pass can add canonical decomposition for the
# shapes we observe in practice.


class ClaudeSDKEntry(_EventBase):
    """Wraps one Claude Agent SDK ``SessionStoreEntry`` verbatim.

    The entry comes from the Claude Code CLI's on-disk JSONL transcript. The
    shape is a discriminated union declared internal by the SDK; we preserve
    it byte-equivalently to guarantee the round-trip invariant
    ``load(append(entries)) == entries``.
    """

    entry_type: str
    """The JSONL entry's ``type`` discriminator (e.g. ``user``, ``assistant``, ``system``)."""

    entry_uuid: str
    """The entry's own ``uuid`` field — used as stable identity across re-appends."""

    entry_timestamp: str
    """ISO-8601 timestamp from the entry (distinct from when we appended it)."""

    raw_entry: dict[str, Any]
    """Full entry dict verbatim — the only durable round-trip guarantee."""

    subpath: str | None = None
    """Subagent transcript suffix, e.g. ``subagents/agent-{id}``. ``None`` for the main transcript."""

    timestamp: datetime
    """When this KurrentDB event was appended."""
