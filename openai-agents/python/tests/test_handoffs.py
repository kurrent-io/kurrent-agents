"""Unit tests for the pure-logic ``_handoffs`` module + stream-name helpers."""

from __future__ import annotations

import pytest

from kurrent_openai_agents._stream_names import for_subsession


def test_for_subsession_uses_canonical_builder() -> None:
    assert for_subsession("sess-1", "sub-x-abc123") == "AgentSubsession-sess-1-sub-x-abc123"


def test_for_subsession_rejects_empty_parent() -> None:
    with pytest.raises(ValueError, match="parent_session_id"):
        for_subsession("", "sub-x-abc")


def test_for_subsession_rejects_empty_agent_id() -> None:
    with pytest.raises(ValueError, match="agent_id"):
        for_subsession("sess-1", "")


def test_for_subsession_normalises_unsafe_chars() -> None:
    # spaces are not in the safe-char set per §2.4 — must be URL-encoded.
    assert for_subsession("sess 1", "sub x") == "AgentSubsession-sess%201-sub%20x"
