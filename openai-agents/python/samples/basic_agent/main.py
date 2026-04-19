"""End-to-end demo of ``KurrentDBSession`` for the OpenAI Agents SDK.

Two turns sharing one ``session_id`` across two fresh ``Runner`` + ``Session``
instances. Turn 1 asks about Tokyo weather; the agent calls ``get_weather``.
Turn 2 (a new Session reading the same stream) asks a follow-up and answers
without a second tool call. A third Session then dumps the full persisted
conversation.

Matches the shape of the ADK and Strands basic_agent samples so the three
integrations compare side by side.

Prerequisites:
    - KurrentDB running:    docker compose up -d
    - Anthropic API key:    export ANTHROPIC_API_KEY=sk-ant-...
    (or replace `LitellmModel` with `model="gpt-4o-mini"` and set `OPENAI_API_KEY`)

Run:
    python -m samples.basic_agent.main          # from openai-agents/python/
"""

from __future__ import annotations

import asyncio
import os
import uuid

from agents import Runner

from kurrent_openai_agents import KurrentDBSession, client as kdb_client

from .agent import build_agent

APP_NAME = "basic_openai_agent_demo"
USER_ID = "alice"


def _summarise(item: dict) -> str:
    """Short description of a persisted session item.

    Handles both input-list shapes the Responses API uses:
    - ``content`` as a plain string (what the Runner wraps user strings into)
    - ``content`` as a typed-parts list (what the model returns)
    """
    kind = item.get("type") or ("message" if "role" in item else "?")
    if kind == "message":
        role = item.get("role", "?")
        content = item.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = next(
                (
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("text")
                ),
                "",
            )
        else:
            text = ""
        return f"{role}: {text[:90]!r}"
    if kind == "function_call":
        return f"fn_call: {item.get('name')}({item.get('arguments')})"
    if kind == "function_call_output":
        return f"fn_result: {str(item.get('output'))[:90]!r}"
    return f"{kind}: …"


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Set ANTHROPIC_API_KEY before running this sample (the agent is "
            "configured to route through LiteLLM → Anthropic)."
        )

    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)

    session_id = f"alice-weather-{uuid.uuid4().hex[:8]}"
    print(f"Session id: {session_id}\n")

    # --- Turn 1: fresh session ------------------------------------------------
    print("=== Turn 1 (fresh session) ===")
    session_1 = KurrentDBSession(
        session_id=session_id, client=kdb, app_name=APP_NAME, user_id=USER_ID
    )
    agent_1 = build_agent()

    question_1 = "What's the weather in Tokyo?"
    print(f"User:  {question_1}")
    result_1 = await Runner.run(agent_1, question_1, session=session_1)
    print(f"Agent: {result_1.final_output}\n")

    # --- Turn 2: same session id, fresh Session + Runner ---------------------
    # Simulates a new process picking up where the previous one left off.
    print("=== Turn 2 (resumed session, new Session instance) ===")
    session_2 = KurrentDBSession(
        session_id=session_id, client=kdb, app_name=APP_NAME, user_id=USER_ID
    )
    agent_2 = build_agent()

    question_2 = "What temperature did you just tell me?"
    print(f"User:  {question_2}")
    result_2 = await Runner.run(agent_2, question_2, session=session_2)
    print(f"Agent: {result_2.final_output}\n")

    # --- Full persisted conversation via a third session ---------------------
    print("=== Persisted session items (via a third Session) ===")
    session_3 = KurrentDBSession(
        session_id=session_id, client=kdb, app_name=APP_NAME, user_id=USER_ID
    )
    items = await session_3.get_items()
    for idx, item in enumerate(items):
        print(f"  [{idx}] {_summarise(item)}")
    print(f"\nTotal persisted items: {len(items)}")

    await kdb.close()


if __name__ == "__main__":
    asyncio.run(main())
