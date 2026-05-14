"""End-to-end demo of canonical handoffs with ``KurrentDBSession`` (AI-471).

A triage agent receives a vague request and hands off to one of two specialist
agents. The session persists the conversation with:

  - parent ``AgentSession-{id}`` stream: triage's turns + ``SubagentStarted`` /
    ``SubagentCompleted`` lifecycle markers for the specialist invocation.
  - ``AgentSubsession-{id}-{agent_id}`` stream: the specialist's transcript.

Demonstrates the ``session=s, hooks=s`` wiring required to promote handoffs
to canonical subagent lifecycle events. Without ``hooks=s`` the same code
degrades — the handoff tool call is still persisted as a regular canonical
tool call (``AssistantToolCallsGenerated`` / ``ToolResultReceived``) on the
parent stream, but the ``SubagentStarted`` / ``SubagentCompleted`` lifecycle
and the subsession transcript stream are not emitted, leaving cross-framework
readers (ADK / MAF / Strands / Capacitor) without the subagent context.

Prerequisites:
    - KurrentDB running:    docker compose up -d
    - OpenAI API key:       export OPENAI_API_KEY=sk-...

Run:
    python -m samples.handoff_demo.main           # from openai-agents/python/
"""

from __future__ import annotations

import asyncio
import uuid

from agents import Agent, Runner

from kurrent_openai_agents import KurrentDBSession
from kurrent_openai_agents import client as kdb_client


async def _amain() -> None:
    kdb = kdb_client.from_connection_string("kurrentdb://localhost:2113?Tls=false")
    session_id = f"handoff-demo-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(
        session_id=session_id, client=kdb, app_name="handoff-demo", user_id="local",
    )

    weather = Agent(
        name="Weather", instructions="You answer only weather questions concisely.",
    )
    history = Agent(
        name="History", instructions="You answer only history questions concisely.",
    )
    triage = Agent(
        name="Triage",
        instructions=(
            "Route the user to Weather for weather questions and History for "
            "history questions. Use the appropriate handoff."
        ),
        handoffs=[weather, history],
    )

    result = await Runner.run(
        triage, "Was the Battle of Hastings rainy?",
        session=session, hooks=session,
    )
    print(f"Final response from {result.last_agent.name}:")
    print(result.final_output)
    print()
    print(f"Stream layout for session {session_id}:")
    print(f"  parent:     AgentSession-{session_id}")
    print(f"  subagent:   AgentSubsession-{session_id}-sub-<role>-<callid>")
    print()
    print("Replay (flat list as the SDK sees it):")
    for i, item in enumerate(await session.get_items()):
        print(f"  [{i}] type={item.get('type'):20s}")


if __name__ == "__main__":
    asyncio.run(_amain())
