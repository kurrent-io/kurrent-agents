"""Domain events persisted to KurrentDB.

These Pydantic models mirror the C# event records field-for-field so that the same
stream is mutually intelligible between C# and Python agents. JSON field names use
``snake_case`` to match the C# default naming policy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _EventBase(BaseModel):
    """Base config shared by all events: snake_case JSON, strict validation."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )


# --- Session lifecycle ---


class SessionStarted(_EventBase):
    agent_name: str | None = None
    model: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    timestamp: datetime


class SessionEnded(_EventBase):
    reason: str | None = None
    timestamp: datetime


# --- Conversation ---


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


class ToolCallInfo(_EventBase):
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


# --- Memory ---


class FactRetained(_EventBase):
    fact: str
    retained_at: datetime


# --- Usage ---


class TokenUsageRecorded(_EventBase):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    additional_counts: dict[str, int] | None = None
    model: str | None = None
    finish_reason: str | None = None
    response_id: str | None = None
    timestamp: datetime


# --- Eval ---


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
