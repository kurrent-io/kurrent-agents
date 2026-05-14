"""Unit tests for the pure-logic ``_handoffs`` module + stream-name helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agents.lifecycle import RunHooksBase
from kurrent_agent_schema import (
    AssistantTextGenerated,
    SubagentCompleted,
    SubagentStarted,
    UserMessageReceived,
)

from kurrent_openai_agents import KurrentDBSession
from kurrent_openai_agents._handoffs import (
    ActiveHandoff,
    DualAppend,
    ExpectedHandoff,
    SingleAppend,
    _HandoffLedger,
    derive_agent_id,
    route_items,
    slug,
)
from kurrent_openai_agents._stream_names import for_subsession

# ----- for_subsession -------------------------------------------------------


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


# ----- slug --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ResearchAgent", "researchagent"),
    ("Code Reviewer", "code-reviewer"),
    ("multi   word", "multi-word"),
    ("-leading-and-trailing-", "leading-and-trailing"),
    ("UPPER_under_score", "upper_under_score"),
    ("with!punct?.", "with-punct"),
    ("", ""),
])
def test_slug_normalises(raw: str, expected: str) -> None:
    assert slug(raw) == expected


# ----- derive_agent_id ---------------------------------------------------

def test_derive_agent_id_format() -> None:
    aid = derive_agent_id("ResearchAgent", "call_abc123def456")
    assert aid == "sub-researchagent-def456"


def test_derive_agent_id_unique_per_call_id() -> None:
    a = derive_agent_id("Spec", "call_111111")
    b = derive_agent_id("Spec", "call_222222")
    assert a != b
    assert a.startswith("sub-spec-") and b.startswith("sub-spec-")


def test_derive_agent_id_handles_short_call_id() -> None:
    aid = derive_agent_id("X", "abc")
    assert aid == "sub-x-abc"


def test_derive_agent_id_rejects_empty_target_name() -> None:
    with pytest.raises(ValueError, match="target_name"):
        derive_agent_id("", "call_1")


def test_derive_agent_id_rejects_empty_call_id() -> None:
    with pytest.raises(ValueError, match="call_id"):
        derive_agent_id("X", "")


# ----- route_items ----------------------------------------------------------


def _make_ledger(parent: str = "AgentSession-test") -> _HandoffLedger:
    return _HandoffLedger(parent_stream=parent)


def _ts() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)


def test_route_items_all_parent_when_no_handoff() -> None:
    ledger = _make_ledger()
    items = [{"type": "message", "role": "user", "content": "hello"}]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )
    assert len(ops) == 1
    assert isinstance(ops[0], SingleAppend)
    assert ops[0].stream == "AgentSession-test"
    assert isinstance(ops[0].events[0], UserMessageReceived)


def test_route_items_emits_subagent_started_when_expected_set() -> None:
    ledger = _make_ledger("AgentSession-sess-1")

    class _FakeAgent:
        name = "ResearchAgent"

    ledger.expected = ExpectedHandoff(
        from_name="TriageAgent", to_name="ResearchAgent", to_agent=_FakeAgent()
    )
    items = [
        {
            "type": "function_call",
            "call_id": "call_xyz123def456",
            "name": "transfer_to_researchagent",
            "arguments": '{"topic": "ACME"}',
        },
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    assert len(ops) == 1
    assert isinstance(ops[0], DualAppend)
    assert ops[0].streams[0] == "AgentSession-sess-1"
    assert ops[0].streams[1].startswith("AgentSubsession-sess-1-sub-researchagent-")
    assert isinstance(ops[0].event, SubagentStarted)
    assert ops[0].event.agent_type == "ResearchAgent"

    assert "call_xyz123def456" in ledger.active
    assert ledger.current_owner.startswith("AgentSubsession-sess-1-sub-researchagent-")
    assert ledger.expected is None


def test_route_items_emits_subagent_completed_on_matching_output() -> None:
    ledger = _make_ledger("AgentSession-sess-1")
    subsession = "AgentSubsession-sess-1-sub-researchagent-def456"
    ledger.active["call_xyz123def456"] = ActiveHandoff(
        call_id="call_xyz123def456",
        agent_id="sub-researchagent-def456",
        agent_type="ResearchAgent",
        subsession_stream=subsession,
    )
    ledger.current_owner = subsession

    items = [
        {
            "type": "function_call_output",
            "call_id": "call_xyz123def456",
            "output": "ACME Q1 summary: ...",
        },
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    assert len(ops) == 1
    assert isinstance(ops[0], DualAppend)
    assert ops[0].streams == ("AgentSession-sess-1", subsession)
    assert isinstance(ops[0].event, SubagentCompleted)
    assert ops[0].event.agent_id == "sub-researchagent-def456"

    assert "call_xyz123def456" not in ledger.active
    assert ledger.current_owner == "AgentSession-sess-1"


def test_route_items_routes_inner_subagent_items_to_subsession() -> None:
    ledger = _make_ledger("AgentSession-sess-1")
    subsession = "AgentSubsession-sess-1-sub-researchagent-def456"
    ledger.active["call_xyz"] = ActiveHandoff(
        call_id="call_xyz",
        agent_id="sub-researchagent-def456",
        agent_type="ResearchAgent",
        subsession_stream=subsession,
    )
    ledger.current_owner = subsession

    items = [
        {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "researched"}],
        },
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=5, timestamp=_ts()
    )

    assert len(ops) == 1
    assert isinstance(ops[0], SingleAppend)
    assert ops[0].stream == subsession
    assert isinstance(ops[0].events[0], AssistantTextGenerated)


def test_route_items_handoff_call_without_expected_falls_through() -> None:
    """Hooks-not-wired degradation: looks like a regular function call -> OpenAIItem."""
    ledger = _make_ledger("AgentSession-sess-1")
    items = [{
        "type": "function_call",
        "call_id": "call_z",
        "name": "transfer_to_x",
        "arguments": "{}",
    }]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    assert all(isinstance(op, SingleAppend) and op.stream == "AgentSession-sess-1" for op in ops)
    flat_events = [e for op in ops for e in op.events]
    assert not any(isinstance(e, SubagentStarted) for e in flat_events)


def test_route_items_preserves_inter_op_ordering() -> None:
    """Ordering invariant: handoff body items appear in ops AFTER the SubagentStarted DualAppend."""
    ledger = _make_ledger("AgentSession-sess-1")

    class _FakeAgent:
        name = "X"

    ledger.expected = ExpectedHandoff(from_name="T", to_name="X", to_agent=_FakeAgent())

    items = [
        {"type": "function_call", "call_id": "call_aaa111", "name": "transfer_to_x", "arguments": "{}"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
        {"type": "function_call_output", "call_id": "call_aaa111", "output": "ok"},
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    assert len(ops) == 3
    assert isinstance(ops[0], DualAppend) and isinstance(ops[0].event, SubagentStarted)
    assert isinstance(ops[1], SingleAppend)
    assert isinstance(ops[1].events[0], AssistantTextGenerated)
    assert isinstance(ops[2], DualAppend) and isinstance(ops[2].event, SubagentCompleted)


# ----- KurrentDBSession as RunHooksBase ----------------------------------


class _StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name


def test_session_is_a_run_hooks_base() -> None:
    # Compose without a real KurrentDB client — we only test the in-memory ledger.
    session = KurrentDBSession(session_id="sess-1", client=None)  # type: ignore[arg-type]
    assert isinstance(session, RunHooksBase)


async def test_on_handoff_sets_expected_handoff() -> None:
    session = KurrentDBSession(session_id="sess-1", client=None)  # type: ignore[arg-type]
    target = _StubAgent("ResearchAgent")
    await session.on_handoff(context=None, from_agent=_StubAgent("Triage"), to_agent=target)
    assert session._ledger.expected is not None
    assert session._ledger.expected.from_name == "Triage"
    assert session._ledger.expected.to_name == "ResearchAgent"
    assert session._ledger.expected.to_agent is target


async def test_duplicate_on_handoff_is_idempotent_while_expected_set() -> None:
    session = KurrentDBSession(session_id="sess-1", client=None)  # type: ignore[arg-type]
    a = _StubAgent("A")
    b = _StubAgent("B")
    await session.on_handoff(context=None, from_agent=a, to_agent=b)
    first = session._ledger.expected
    await session.on_handoff(context=None, from_agent=a, to_agent=b)
    # Second call is dropped while expected is still pending.
    assert session._ledger.expected is first
