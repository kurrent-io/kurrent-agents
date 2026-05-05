"""OpenAI-Agents-specific framework events.

These events are persisted alongside canonical events in
``AgentSession-{session_id}`` streams. They are **not** part of the shared
canonical schema (`SCHEMA_v2.md`); cross-framework readers (ADK / MAF /
Strands / Claude SDK) skip them on read.

The shared :mod:`kurrent_agent_schema` package provides the canonical event
types as protobuf messages. Framework-specific events keep using Pydantic
because they are not part of the cross-framework wire contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

OPENAI_EXTENSION_KEY: str = "openai"
"""Slug under which OpenAI-specific fields ride on canonical events'
``extensions`` map. See ``schema/SCHEMA_v2.md §5``."""


class _OpenAIEventBase(BaseModel):
    """Shared config for OpenAI-Agents framework-specific events.

    ``extra="ignore"`` keeps reads forward-compatible. ``frozen`` gives
    value-type semantics. Bytes round-trip as base64 in JSON for parity
    with the v1 canonical events that used the same configuration.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    extensions: dict[str, dict[str, Any]] | None = None
    """Framework-extension envelope. Mirrors canonical events for parity,
    even though OpenAI rarely populates it on its own framework events."""


class OpenAIItem(_OpenAIEventBase):
    """Wraps a non-canonical OpenAI Agents SDK session item verbatim.

    Canonical conversation items (user/assistant text messages, tool calls,
    tool results, reasoning, MCP approvals) are decomposed into shared
    canonical events. This type exists for items that have no canonical
    analogue today — handoff_call/handoff_output (DEV-1684), computer_call,
    shell_call, web_search, etc. — so they still round-trip through
    ``get_items`` / ``add_items`` without loss.
    """

    item_type: str
    """The OpenAI SDK item's ``type`` field."""

    raw_item: dict[str, Any]
    """Full item dict verbatim for lossless reconstruction on ``get_items``."""

    message_index: int
    """Monotonic session-index assigned on write."""

    timestamp: datetime
