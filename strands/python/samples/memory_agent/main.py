"""Cross-session memory demo backed by ``KurrentDBAgentMemory``.

Two sessions, different ``session_id``s, same ``(app_name, user_id)`` scope.

- Session A: user introduces themselves. Agent calls ``remember`` once per
  distinct fact → canonical ``FactRetained`` events land on
  ``AgentMemory-{app}-{user}``.
- Session B: different session, no shared chat history. Agent calls
  ``recall_memory``, receives the retained facts, and answers from them.

Prerequisites:
    docker compose up -d
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python -m samples.memory_agent.main
"""

from __future__ import annotations

import os
import uuid

from kurrent_strands import (
    KurrentDBAgentMemory,
    KurrentDBSessionManager,
    client as kdb_client,
)

from .agent import build_agent

APP_NAME = "memory_strands_agent_demo"
AGENT_ID = "memory_strands_agent"


def _text_of(message) -> str:
    for block in message.get("content") or []:
        if block.get("text"):
            return block["text"]
    return ""


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running this sample.")

    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)

    # Fresh (user, session) ids per run so repeat invocations don't collide.
    user_id = f"alice_{uuid.uuid4().hex[:8]}"
    session_a = f"intro-{uuid.uuid4().hex[:6]}"
    session_b = f"followup-{uuid.uuid4().hex[:6]}"
    print(f"user_id={user_id}")
    print(f"session_a={session_a}  session_b={session_b}\n")

    memory = KurrentDBAgentMemory(
        kdb, app_name=APP_NAME, user_id=user_id
    )

    # --- Session A ------------------------------------------------------------
    print("=== Session A (introductions) ===")
    sm_a = KurrentDBSessionManager(
        client=kdb,
        session_id=session_a,
        app_name=APP_NAME,
        user_id=user_id,
        agent_name=AGENT_ID,
    )
    agent_a = build_agent(
        session_manager=sm_a, memory=memory, agent_id=AGENT_ID
    )

    intro = (
        "Hi! My name is Alexey, I work at Kurrent building an event-sourced "
        "agent platform, and I prefer dark mode IDEs."
    )
    print(f"User:  {intro}")
    result_a = agent_a(intro)
    print(f"Agent: {_text_of(result_a.message)}\n")

    # --- Inspect what actually landed in memory ------------------------------
    facts = memory.recall()
    print(f"=== Retained facts ({len(facts)} entries) ===")
    for fact in facts:
        print(f"  - {fact}")
    print()

    # --- Session B: new session_id, same user_id -----------------------------
    print("=== Session B (new session, same user) ===")
    sm_b = KurrentDBSessionManager(
        client=kdb,
        session_id=session_b,
        app_name=APP_NAME,
        user_id=user_id,
        agent_name=AGENT_ID,
    )
    agent_b = build_agent(
        session_manager=sm_b, memory=memory, agent_id=AGENT_ID
    )
    assert len(agent_b.messages) == 0, (
        "Expected Session B to start with no shared messages — different session_id"
    )

    question = "What do you know about me?"
    print(f"User:  {question}")
    result_b = agent_b(question)
    print(f"Agent: {_text_of(result_b.message)}")


if __name__ == "__main__":
    main()
