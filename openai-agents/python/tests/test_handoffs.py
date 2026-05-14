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


def test_route_items_function_call_output_routes_as_tool_result_to_subsession() -> None:
    """function_call_output for a handoff call_id now produces ToolResultReceived on
    the subsession, NOT SubagentCompleted. The subagent stays open — SubagentCompleted
    is emitted later via pending_close drain."""
    from kurrent_agent_schema import ToolResultReceived

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

    # Should be a SingleAppend with ToolResultReceived — not a DualAppend(SubagentCompleted)
    assert len(ops) == 1
    assert isinstance(ops[0], SingleAppend)
    assert ops[0].stream == subsession
    assert len(ops[0].events) == 1
    assert isinstance(ops[0].events[0], ToolResultReceived)

    # Active should still be set — not yet closed
    assert "call_xyz123def456" in ledger.active
    # current_owner remains the subsession
    assert ledger.current_owner == subsession


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


def test_route_items_preserves_inter_op_ordering_real_sdk_order() -> None:
    """Ordering invariant using the REAL SDK order: [handoff_call, handoff_output, sub_msg].

    The SDK emits handoff_output as a synthetic ack immediately after handoff_call
    (part of handoff SETUP), then the target's items follow in subsequent batches.
    Both handoff_output and sub_msg should land on the subsession; the subagent
    remains open (no SubagentCompleted emitted here — that comes from pending_close).
    """
    from kurrent_agent_schema import ToolResultReceived

    ledger = _make_ledger("AgentSession-sess-1")

    class _FakeAgent:
        name = "X"

    ledger.expected = ExpectedHandoff(from_name="T", to_name="X", to_agent=_FakeAgent(), tool_name="transfer_to_x")

    items = [
        {"type": "function_call", "call_id": "call_aaa111", "name": "transfer_to_x", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_aaa111", "output": "handoff-ack"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    # Op 0: DualAppend(SubagentStarted) on parent + subsession
    assert isinstance(ops[0], DualAppend) and isinstance(ops[0].event, SubagentStarted)
    # Op 1: SingleAppend on subsession with ToolResultReceived (handoff_output) + AssistantTextGenerated (sub_msg)
    assert isinstance(ops[1], SingleAppend)
    subsession = ops[0].streams[1]
    assert ops[1].stream == subsession
    assert isinstance(ops[1].events[0], ToolResultReceived)
    assert isinstance(ops[1].events[1], AssistantTextGenerated)

    # Active still set (subagent not yet closed)
    assert "call_aaa111" in ledger.active
    assert ledger.current_owner == subsession


def test_route_items_drains_pending_close() -> None:
    """pending_close entries are drained at the end of route_items, emitting
    DualAppend(SubagentCompleted). Active is cleared and current_owner flips to parent."""
    ledger = _make_ledger("AgentSession-sess-1")
    subsession = "AgentSubsession-sess-1-sub-researchagent-def456"
    call_id = "call_xyz123def456"
    ledger.active[call_id] = ActiveHandoff(
        call_id=call_id,
        agent_id="sub-researchagent-def456",
        agent_type="ResearchAgent",
        subsession_stream=subsession,
    )
    ledger.current_owner = subsession
    ledger.pending_close[call_id] = "final output"

    # Call with empty items — only the pending_close drain should fire.
    ops = route_items(
        [], ledger=ledger, session_id="sess-1", start_index=5, timestamp=_ts()
    )

    assert len(ops) == 1
    assert isinstance(ops[0], DualAppend)
    assert ops[0].streams == ("AgentSession-sess-1", subsession)
    assert isinstance(ops[0].event, SubagentCompleted)
    assert ops[0].event.agent_id == "sub-researchagent-def456"
    assert ops[0].event.summary == "final output"

    # Active and pending_close both cleared.
    assert call_id not in ledger.active
    assert call_id not in ledger.pending_close
    # current_owner flipped back to parent.
    assert ledger.current_owner == "AgentSession-sess-1"


def test_route_items_drains_pending_close_after_final_items() -> None:
    """When both items AND pending_close are present, items flush first,
    then SubagentCompleted closes — correct ordering."""
    ledger = _make_ledger("AgentSession-sess-1")
    subsession = "AgentSubsession-sess-1-sub-x-aaaaaa"
    call_id = "call_final"
    ledger.active[call_id] = ActiveHandoff(
        call_id=call_id,
        agent_id="sub-x-aaaaaa",
        agent_type="X",
        subsession_stream=subsession,
    )
    ledger.current_owner = subsession
    ledger.pending_close[call_id] = "done"

    items = [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "final"}]},
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=3, timestamp=_ts()
    )

    # Op 0: SingleAppend (AssistantTextGenerated on subsession)
    assert isinstance(ops[0], SingleAppend)
    assert ops[0].stream == subsession
    # Op 1: DualAppend(SubagentCompleted) — always last
    assert isinstance(ops[1], DualAppend)
    assert isinstance(ops[1].event, SubagentCompleted)


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
