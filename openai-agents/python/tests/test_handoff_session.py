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

import pytest
from agents import Agent, Runner
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_openai_agents import KurrentDBSession
from kurrent_openai_agents._serialization import deserialize
from kurrent_openai_agents._stream_names import for_session

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="Requires OPENAI_API_KEY for live Runner.run; see Task 10 for offline coverage.",
    ),
]


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
