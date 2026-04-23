"""Integration-specific events for the Claude Agent SDK adapter.

``ClaudeSDKEntry`` is the verbatim envelope for one opaque JSONL line the
Claude Code CLI writes to disk. The shape is declared internal-and-unstable
by the SDK, so we preserve it deep-equal (per the SDK's
``load(append(entries)) == entries`` guarantee) rather than decomposing into
canonical events at write time. Read-side decomposition lives in
:mod:`kurrent_claude_agent_sdk.decompose`.

Canonical schema types (``SessionStarted``, ``UserMessageReceived``,
``AssistantTextGenerated``, …) come from :mod:`kurrent_agent_schema` —
imported directly where needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

CLAUDE_SDK_EXTENSION_KEY: str = "claude_sdk"
"""Extension-envelope slug owned by this integration. See
``schema/SCHEMA_v2.md §5.2``. Note: ``claude_code`` is a separate, Capacitor-
owned slug — not this integration's."""


class ClaudeSDKEntry(BaseModel):
    """Wraps one Claude Agent SDK ``SessionStoreEntry`` verbatim.

    The entry comes from the Claude Code CLI's on-disk JSONL transcript. The
    shape is a discriminated union declared internal by the SDK; we preserve
    it deep-equal to guarantee the round-trip invariant
    ``load(append(entries)) == entries``. Forward-compatibility with later
    schema revisions rides on ``extra="ignore"`` for any future fields.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )

    entry_type: str
    """The JSONL entry's ``type`` discriminator (e.g. ``user``, ``assistant``, ``system``)."""

    entry_uuid: str
    """The entry's own ``uuid`` field — used as stable identity across re-appends."""

    entry_timestamp: str
    """ISO-8601 timestamp from the entry (distinct from when we appended it)."""

    raw_entry: dict[str, Any]
    """Full entry dict verbatim — the only durable round-trip guarantee."""

    subpath: str | None = None
    """Subagent transcript suffix from ``SessionKey.subpath`` (e.g.
    ``subagents/agent-{id}``). ``None`` for the main transcript."""

    timestamp: datetime
    """When this KurrentDB event was appended."""

    extensions: dict[str, dict[str, Any]] | None = None
    """Framework-specific extension envelope keyed by slug. See
    ``schema/SCHEMA_v2.md §5``."""
