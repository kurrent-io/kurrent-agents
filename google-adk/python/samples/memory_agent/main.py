"""Cross-session memory demo backed by ``KurrentDBMemoryService``.

Two sessions, different ``session_id``s, same ``(app_name, user_id)`` scope:

- Session A: user introduces themselves. Agent calls ``remember`` once per
  distinct fact → canonical ``FactRetained`` events land in
  ``AgentMemory-{app}-{user}``.
- Session B: new session, no shared history. Agent calls ``load_memory`` and
  answers "what do you know about me?" from the retained facts.

Prerequisites:
    docker compose up -d
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python -m samples.memory_agent.main
"""

from __future__ import annotations

import asyncio
import os
import uuid

from google.adk.apps import App
from google.adk.runners import Runner
from google.genai import types

from kurrent_google_adk import (
    KurrentDBMemoryService,
    KurrentDBSessionService,
    client as kdb_client,
)

from .agent import root_agent

APP_NAME = "memory_agent_demo"


async def ask(runner: Runner, *, user_id: str, session_id: str, message: str) -> str:
    content = types.Content(role="user", parts=[types.Part(text=message)])
    parts: list[str] = []
    tool_calls: list[str] = []
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        # Log tool calls the agent makes during this turn.
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call:
                    fc = part.function_call
                    tool_calls.append(f"{fc.name}({fc.args or {}})")
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    parts.append(part.text)
    for call in tool_calls:
        print(f"       └── tool: {call}")
    return "".join(parts).strip()


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running this sample.")

    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)

    # Isolate per-run so memory doesn't bleed between repeated invocations.
    user_id = f"alice_{uuid.uuid4().hex[:8]}"
    session_a = f"intro-{uuid.uuid4().hex[:6]}"
    session_b = f"followup-{uuid.uuid4().hex[:6]}"
    print(f"user_id={user_id}")
    print(f"session_a={session_a}, session_b={session_b}\n")

    session_service = KurrentDBSessionService(kdb)
    memory_service = KurrentDBMemoryService(kdb)

    def make_runner() -> Runner:
        return Runner(
            app=App(name=APP_NAME, root_agent=root_agent),
            session_service=session_service,
            memory_service=memory_service,
        )

    # --- Session A: user introduces themselves ------------------------------
    print("=== Session A (introductions) ===")
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_a
    )
    runner_a = make_runner()

    intro = (
        "Hi! My name is Alexey, I work at Kurrent building an event-sourced "
        "agent platform, and I prefer dark mode IDEs."
    )
    print(f"User:  {intro}")
    a1 = await ask(
        runner_a, user_id=user_id, session_id=session_a, message=intro
    )
    print(f"Agent: {a1}\n")

    # --- Inspect what landed in memory --------------------------------------
    result = await memory_service.search_memory(
        app_name=APP_NAME, user_id=user_id, query=""
    )
    print(f"=== Retained facts ({len(result.memories)} entries) ===")
    for entry in result.memories:
        text = entry.content.parts[0].text if entry.content.parts else ""
        print(f"  - {text}")
    print()

    # --- Session B: new session, ask what the agent knows --------------------
    print("=== Session B (new session, same user) ===")
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_b
    )
    runner_b = make_runner()

    question = "What do you know about me?"
    print(f"User:  {question}")
    b1 = await ask(
        runner_b, user_id=user_id, session_id=session_b, message=question
    )
    print(f"Agent: {b1}")

    await kdb.close()


if __name__ == "__main__":
    asyncio.run(main())
