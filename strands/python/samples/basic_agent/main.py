"""End-to-end demo of ``KurrentDBSessionManager``.

Two turns against the same ``session_id`` across two fresh ``Agent`` +
``KurrentDBSessionManager`` instances. Turn 1 asks about Tokyo weather and
expects a ``get_weather`` tool call. Turn 2 (a new Agent reading the session
from KurrentDB) asks a follow-up and answers without hitting the tool again.

Matches the shape of the ADK ``basic_agent`` sample so the two integrations
can be compared side by side.

Prerequisites:
    - KurrentDB running:    docker compose up -d
    - Anthropic API key:    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python -m samples.basic_agent.main          # from strands/python/
"""

from __future__ import annotations

import os
import uuid

from kurrent_strands import KurrentDBSessionManager, client as kdb_client

from .agent import build_agent


AGENT_ID = "basic_strands_agent"


def _summarise_message(msg) -> str:
    """One-line description of a Strands Message for the demo output."""
    bits: list[str] = []
    for block in msg.get("content") or []:
        if "text" in block:
            text = (block["text"] or "")[:80].replace("\n", " ")
            bits.append(f"text={text!r}")
        if "toolUse" in block:
            tu = block["toolUse"]
            bits.append(f"fn_call={tu['name']}({tu.get('input') or {}})")
        if "toolResult" in block:
            tr = block["toolResult"]
            bits.append(f"fn_response={tr.get('content')}")
    usage = (msg.get("metadata") or {}).get("usage")
    if usage:
        bits.append(
            f"usage=in:{usage.get('inputTokens')} out:{usage.get('outputTokens')}"
        )
    return " | ".join(bits) or "(empty)"


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running this sample.")

    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)

    session_id = f"alice-weather-{uuid.uuid4().hex[:8]}"
    print(f"Session id: {session_id}\n")

    # --- Turn 1: fresh session ------------------------------------------------
    print("=== Turn 1 (fresh session) ===")
    sm_1 = KurrentDBSessionManager(
        client=kdb,
        session_id=session_id,
        app_name="basic_strands_agent_demo",
        user_id="alice",
        agent_name="basic_strands_agent",
    )
    agent_1 = build_agent(sm_1, agent_id=AGENT_ID)

    question_1 = "What's the weather in Tokyo?"
    print(f"User:  {question_1}")
    result_1 = agent_1(question_1)
    print(f"Agent: {_text_of(result_1.message)}\n")

    # --- Turn 2: same session id, fresh Agent + SessionManager ---------------
    # Simulates a new process picking up where the previous one left off.
    print("=== Turn 2 (resumed session, new Agent instance) ===")
    sm_2 = KurrentDBSessionManager(
        client=kdb,
        session_id=session_id,
        app_name="basic_strands_agent_demo",
        user_id="alice",
        agent_name="basic_strands_agent",
    )
    agent_2 = build_agent(sm_2, agent_id=AGENT_ID)
    assert len(agent_2.messages) > 0, "Expected initialize() to restore messages"

    question_2 = "What temperature did you just tell me?"
    print(f"User:  {question_2}")
    result_2 = agent_2(question_2)
    print(f"Agent: {_text_of(result_2.message)}\n")

    # --- Full persisted conversation as seen by a third fresh manager --------
    print("=== Persisted conversation (via a third SessionManager) ===")
    sm_3 = KurrentDBSessionManager(
        client=kdb,
        session_id=session_id,
        app_name="basic_strands_agent_demo",
        user_id="alice",
    )
    agent_3 = build_agent(sm_3, agent_id=AGENT_ID)
    for idx, msg in enumerate(agent_3.messages):
        print(f"  [{idx}] {msg['role']}: {_summarise_message(msg)}")
    print(f"\nTotal persisted messages: {len(agent_3.messages)}")


def _text_of(message) -> str:
    for block in message.get("content") or []:
        if block.get("text"):
            return block["text"]
    return ""


if __name__ == "__main__":
    main()
