"""Strands-specific framework events.

These events are persisted alongside canonical events in
``AgentSession-{session_id}`` streams. They are **not** part of the shared
canonical schema (`SCHEMA_v2.md`); cross-framework readers (ADK / MAF) skip
them on read.

The shared `kurrent_agent_schema` package provides the canonical event types
as protobuf messages. Strands-specific events keep using Pydantic because
they are not part of the cross-framework wire contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrandsEventBase(BaseModel):
    """Shared config for Strands framework-specific events.

    Mirrors the configuration used for canonical events on the v1 Pydantic
    models (``extra="ignore"`` for forward-compat reads, ``frozen`` for
    value-type semantics, base64 round-trip for ``bytes`` fields).
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    extensions: dict[str, dict[str, Any]] | None = None
    """Framework-specific extension envelope. Mirrors canonical events for
    parity, even though Strands rarely populates it on its own events."""


class StrandsAgentState(_StrandsEventBase):
    """Serialized Strands ``SessionAgent`` — emitted on every ``sync_agent``.

    Carries user-managed state, conversation-manager state, and Strands'
    internal state (interrupt + model state). Readers take the latest
    instance per session to rebuild the runtime agent.
    """

    agent_id: str
    state: dict[str, Any] = Field(default_factory=dict)
    conversation_manager_state: dict[str, Any] = Field(default_factory=dict)
    internal_state: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class MessageRedacted(_StrandsEventBase):
    """Marks a prior message as redacted by a guardrail.

    ``redact_message`` carries the replacement content; readers replace the
    original message at the indicated invocation / message index.
    """

    message_index: int
    redact_message: dict[str, Any]
    timestamp: datetime
