"""End-to-end demo of ``KurrentDBSessionService``.

Runs two turns against the same session id across two fresh ``Runner``
instances. Turn 1 asks about the weather and expects the agent to call
``get_weather``. Turn 2 asks a follow-up about the same temperature; the
persisted history makes the answer possible without a second tool call.

Prerequisites:
    - KurrentDB running:    docker compose up -d
    - Anthropic API key:    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python -m samples.basic_agent.main          # from the python/ folder
    # or, with the venv activated:
    python samples/basic_agent/main.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

from google.adk.apps import App
from google.adk.runners import Runner
from google.genai import types

from kurrent_google_adk import KurrentDBSessionService, client as kdb_client

from .agent import root_agent

APP_NAME = "basic_agent_demo"
USER_ID = "alice"


async def ask(runner: Runner, session_id: str, message: str) -> str:
    """Send a message; collect the final assistant text response."""
    content = types.Content(role="user", parts=[types.Part(text=message)])
    parts: list[str] = []
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    parts.append(part.text)
    return "".join(parts).strip()


def _summarise_event(event) -> str:
    """One-line description for a persisted ADK event."""
    bits: list[str] = []
    if event.content and event.content.parts:
        for part in event.content.parts:
            if part.text:
                text = part.text[:80].replace("\n", " ")
                bits.append(f"text={text!r}")
            elif part.function_call:
                fc = part.function_call
                bits.append(f"fn_call={fc.name}({fc.args or {}})")
            elif part.function_response:
                fr = part.function_response
                bits.append(f"fn_response={fr.name}={fr.response}")
    usage = event.usage_metadata
    if usage is not None:
        bits.append(
            f"usage=in:{usage.prompt_token_count} "
            f"out:{usage.candidates_token_count}"
        )
    return " | ".join(bits) or "(empty)"


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running this sample.")

    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)
    session_service = KurrentDBSessionService(kdb)

    # Unique session id per run so repeat invocations don't collide.
    session_id = f"alice-weather-{uuid.uuid4().hex[:8]}"
    print(f"Session id: {session_id}\n")

    # --- Turn 1: fresh session ------------------------------------------------
    print("=== Turn 1 (fresh session) ===")
    await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    app = App(name=APP_NAME, root_agent=root_agent)
    runner = Runner(app=app, session_service=session_service)

    question_1 = "What's the weather in Tokyo?"
    print(f"User:  {question_1}")
    answer_1 = await ask(runner, session_id, question_1)
    print(f"Agent: {answer_1}\n")

    # --- Turn 2: same session id, new Runner ---------------------------------
    # Simulates a new process picking up where the previous one left off.
    print("=== Turn 2 (resumed session, new Runner instance) ===")
    session_service_2 = KurrentDBSessionService(kdb)
    runner_2 = Runner(
        app=App(name=APP_NAME, root_agent=root_agent),
        session_service=session_service_2,
    )
    question_2 = "What temperature did you just tell me?"
    print(f"User:  {question_2}")
    answer_2 = await ask(runner_2, session_id, question_2)
    print(f"Agent: {answer_2}\n")

    # --- Raw event stream ----------------------------------------------------
    print("=== Persisted events ===")
    reloaded = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    assert reloaded is not None
    for idx, event in enumerate(reloaded.events):
        print(f"  [{idx}] {event.author}: {_summarise_event(event)}")
    print(f"\nTotal persisted events: {len(reloaded.events)}")

    await kdb.close()


if __name__ == "__main__":
    asyncio.run(main())
