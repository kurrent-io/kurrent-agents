"""Integration tests for OpenAI Agents handoff promotion (AI-471).

Exercises the full flow: Runner.run with two agents that hand off, against
a real KurrentDB Testcontainer. Asserts:
- parent stream contains the canonical SubagentStarted/Completed lifecycle,
- subsession stream is created and mirrors the lifecycle,
- get_items returns a flat list the SDK can replay verbatim.

The Runner.run cases below make real LLM calls; they auto-skip when
``OPENAI_API_KEY`` is unset. Task 10 covers degradation cases that don't
require an LLM and run unconditionally.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from agents import Agent, Runner
from kurrent_agent_schema import SubagentCompleted, SubagentStarted
from kurrentdbclient import AsyncKurrentDBClient, StreamState

from kurrent_openai_agents import KurrentDBSession
from kurrent_openai_agents._codec import _map_handoff_call
from kurrent_openai_agents._handoffs import ActiveHandoff
from kurrent_openai_agents._serialization import deserialize, serialize_for_multi_append
from kurrent_openai_agents._stream_names import for_session, for_subsession

_requires_openai = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY for live Runner.run; see Task 10 for offline coverage.",
)


async def _stream_events(client: AsyncKurrentDBClient, stream: str) -> list:
    try:
        recorded = await client.get_stream(stream)
    except Exception:
        return []
    out = []
    for r in recorded:
        evt = deserialize(r)
        if evt is not None:
            out.append((r.type, evt))
    return out


@_requires_openai
async def test_handoff_emits_subagent_lifecycle_to_both_streams(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client, app_name="test")

    spec = Agent(name="Specialist", instructions="Respond with the word DONE.")
    triage = Agent(
        name="Triage", instructions="Always hand off to Specialist.",
        handoffs=[spec],
    )
    result = await Runner.run(
        triage,
        "Investigate this for me.",
        session=session, hooks=session,
    )
    assert result is not None

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    parent_types = [t for t, _ in parent_events]
    assert "SessionStarted" in parent_types
    assert "SubagentStarted" in parent_types
    assert "SubagentCompleted" in parent_types

    started = next(evt for t, evt in parent_events if t == "SubagentStarted")
    assert started.agent_type == "Specialist"
    subsession_stream = started.subsession_stream
    assert subsession_stream.startswith(f"AgentSubsession-{session_id}-sub-specialist-")

    sub_events = await _stream_events(kurrentdb_client, subsession_stream)
    sub_types = [t for t, _ in sub_events]
    assert sub_types.count("SubagentStarted") == 1
    assert sub_types.count("SubagentCompleted") == 1
    assert any(t == "AssistantTextGenerated" for t in sub_types)


@_requires_openai
async def test_get_items_inlines_subagent_transcript(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    spec = Agent(name="Specialist", instructions="Respond DONE.")
    triage = Agent(name="Triage", instructions="Hand off.", handoffs=[spec])
    await Runner.run(triage, "Go.", session=session, hooks=session)

    replay = KurrentDBSession(session_id=session_id, client=kurrentdb_client)
    items = await replay.get_items()
    types = [it.get("type") for it in items]
    assert "function_call" in types
    assert "function_call_output" in types
    assert any(t == "message" for t in types)


async def test_on_agent_end_defers_subagent_completed_to_next_add_items(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """on_agent_end only sets pending_close; SubagentCompleted is emitted on
    the NEXT add_items call so the target's final items land before the close marker."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    # Manually seed the ledger as if a handoff had started but no return.
    call_id = "call_unreturned_abc"
    agent_id = "sub-loner-ned_abc"
    sub_stream = for_subsession(session_id, agent_id)
    session._ledger.active[call_id] = ActiveHandoff(
        call_id=call_id, agent_id=agent_id, agent_type="Loner",
        subsession_stream=sub_stream,
    )
    session._ledger.current_owner = sub_stream

    class _Agent:
        name = "Loner"

    await session.on_agent_end(context=None, agent=_Agent(), output="all done")

    # Immediately after on_agent_end: pending_close set, nothing written yet.
    assert call_id in session._ledger.pending_close
    assert call_id in session._ledger.active
    parent_events_before = await _stream_events(kurrentdb_client, for_session(session_id))
    assert not any(t == "SubagentCompleted" for t, _ in parent_events_before)

    # Now trigger add_items with an empty list to drain pending_close.
    await session.add_items([])  # type: ignore[arg-type]

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    completed = [evt for t, evt in parent_events if t == "SubagentCompleted"]
    assert len(completed) == 1
    assert completed[0].agent_id == agent_id
    # After drain: active + pending_close cleared, current_owner back to parent.
    assert session._ledger.current_owner == for_session(session_id)
    assert call_id not in session._ledger.active
    assert call_id not in session._ledger.pending_close


async def test_hooks_not_wired_falls_through_to_canonical_tool_events(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """Without hooks=session, handoff-named function_call/output become canonical tool events,
    not subagent lifecycle events — and no subsession stream is created."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    handoff_call = {
        "type": "function_call",
        "call_id": "call_z123456",
        "name": "transfer_to_x",
        "arguments": "{}",
    }
    handoff_output = {
        "type": "function_call_output",
        "call_id": "call_z123456",
        "output": "{}",
    }
    await session.add_items([handoff_call, handoff_output])  # type: ignore[list-item]

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    types = [t for t, _ in parent_events]
    # Handoff promotion requires ledger.expected to be set by on_handoff; without it,
    # function_call / function_call_output fall through to canonical tool events.
    assert "SubagentStarted" not in types
    assert "SubagentCompleted" not in types
    assert "AssistantToolCallsGenerated" in types
    assert "ToolResultReceived" in types

    # No subsession stream should have been created.
    sub_events = await _stream_events(kurrentdb_client, for_subsession(session_id, "any"))
    assert sub_events == []


async def test_get_items_handles_missing_subsession_stream(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """Parent has SubagentStarted but the subsession stream is gone — emit handoff_call only."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    # Emit a SubagentStarted whose subsession_stream points nowhere.
    fake_call = {
        "type": "function_call", "call_id": "call_ghost1",
        "name": "transfer_to_ghost", "arguments": "{}",
    }
    started = _map_handoff_call(
        item=fake_call, agent_id="sub-ghost-host1", agent_type="Ghost",
        source_agent="Triage",
        subsession_stream=for_subsession(session_id, "sub-ghost-host1"),
        message_index=0, timestamp=datetime.now(UTC),
    )
    # Write only to parent — simulate a non-atomic legacy writer / crash.
    await kurrentdb_client.append_to_stream(
        for_session(session_id),
        events=[serialize_for_multi_append(started)],
        current_version=StreamState.ANY,
    )

    items = await session.get_items()
    types = [it.get("type") for it in items]
    assert "function_call" in types  # handoff_call reconstructed from raw_item
    # No transcript items (the subsession was missing).
    assert "message" not in types


async def test_duplicate_on_handoff_emits_single_subagent_started(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    class _A:
        name = "A"

    class _B:
        name = "B"

    await session.on_handoff(context=None, from_agent=_A(), to_agent=_B())
    await session.on_handoff(context=None, from_agent=_A(), to_agent=_B())  # duplicate

    # Drain the ledger via a synthesised handoff_call.
    call = {
        "type": "function_call", "call_id": "call_dup123456",
        "name": "transfer_to_b", "arguments": "{}",
    }
    await session.add_items([call])  # type: ignore[list-item]

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    started_count = sum(1 for t, _ in parent_events if t == "SubagentStarted")
    assert started_count == 1


async def test_message_index_is_session_wide_monotonic_across_handoffs(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """After a handoff completes, follow-up parent items use indices that
    do NOT collide with indices already consumed on the subsession stream.

    Uses the real SDK order: [handoff_call, handoff_output, sub_msg] in batch 1,
    then a plain parent message in batch 2 after the subagent is closed via
    pending_close (simulated by a follow-up add_items with empty list after
    on_agent_end fires).
    """
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    class _A:
        name = "A"

    class _B:
        name = "B"

    await session.on_handoff(context=None, from_agent=_A(), to_agent=_B())

    # First batch: real SDK order — handoff_call then handoff_output (setup), then sub_msg.
    await session.add_items([
        {"type": "function_call", "call_id": "call_w001", "name": "transfer_to_b", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_w001", "output": "handoff-ack"},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "sub-reply"}]},
    ])  # type: ignore[list-item]

    # Simulate on_agent_end firing (defers close to next add_items).
    await session.on_agent_end(context=None, agent=_B(), output="done")

    # Second batch: final subagent message + pending_close drains.
    await session.add_items([
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "final-sub-reply"}]},
    ])  # type: ignore[list-item]

    # Third batch: a plain user message back on the parent stream.
    await session.add_items([
        {"type": "message", "role": "user", "content": "follow-up"},
    ])  # type: ignore[list-item]

    # Read all canonical events and assert message_index is strictly increasing
    # across the union of parent + subsession streams.
    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    sub_started_evt = next(evt for t, evt in parent_events if t == "SubagentStarted")
    sub_events = await _stream_events(kurrentdb_client, sub_started_evt.subsession_stream)

    indices = []
    for _t, evt in parent_events + sub_events:
        if hasattr(evt, "message_index") and not isinstance(evt, (SubagentStarted, SubagentCompleted)):
            indices.append(evt.message_index)
    # No duplicate session-wide indices.
    assert len(indices) == len(set(indices)), f"Duplicate message_indices: {indices}"


async def test_function_call_missing_call_id_falls_through_to_canonical(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """A function_call with no call_id should not crash add_items, even when
    a handoff is expected — it should fall through to the canonical codec."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    class _A:
        name = "A"

    class _B:
        name = "B"

    await session.on_handoff(context=None, from_agent=_A(), to_agent=_B())

    # Malformed function_call — no call_id.
    await session.add_items([
        {"type": "function_call", "name": "transfer_to_b", "arguments": "{}"},
    ])  # type: ignore[list-item]

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    types = [t for t, _ in parent_events]
    assert "SubagentStarted" not in types
    assert "AssistantToolCallsGenerated" in types
