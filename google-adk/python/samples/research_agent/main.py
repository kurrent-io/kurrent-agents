"""Research-agent demo — exercises session + memory + artifacts in one flow.

Two sessions, same ``(app_name, user_id)`` scope.

**Session A** builds up state:

- Turn 1: user shares notes on event sourcing → agent calls ``save_note`` +
  ``remember``.
- Turn 2: user shares notes on KurrentDB → another ``save_note`` +
  ``remember``.
- Turn 3: user updates the event-sourcing notes → ``save_note`` again; now
  there are two versions of that artifact.
- Turn 4: user asks what notes they have → ``list_notes``.

**Session B** (different ``session_id``, no in-context history):

- Turn 5: user asks "what have I been researching?" → agent calls
  ``load_memory``, replies from the retained facts.
- Turn 6: user asks to see the event-sourcing notes → agent calls
  ``load_note``, replies with the content.

Prerequisites::

    docker compose up -d
    export ANTHROPIC_API_KEY=sk-ant-...

Run::

    python -m samples.research_agent.main
"""

from __future__ import annotations

import asyncio
import os
import uuid

from google.adk.apps import App
from google.adk.runners import Runner
from google.genai import types

from kurrent_google_adk import (
    KurrentDBArtifactService,
    KurrentDBMemoryService,
    KurrentDBSessionService,
    client as kdb_client,
)

from .agent import root_agent

APP_NAME = "research_agent_demo"


async def ask(runner: Runner, *, user_id: str, session_id: str, message: str) -> str:
    content = types.Content(role="user", parts=[types.Part(text=message)])
    final_parts: list[str] = []
    tool_calls: list[str] = []
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call is not None:
                    fc = part.function_call
                    tool_calls.append(f"{fc.name}({fc.args or {}})")
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_parts.append(part.text)
    for call in tool_calls:
        # Truncate noisy tool args for readability.
        pretty = call if len(call) < 110 else call[:107] + "..."
        print(f"       └── tool: {pretty}")
    return "".join(final_parts).strip()


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running this sample.")

    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)

    # Isolated per-run scope so repeated invocations don't mingle.
    user_id = f"alice_{uuid.uuid4().hex[:8]}"
    session_a = f"research-{uuid.uuid4().hex[:6]}"
    session_b = f"research-{uuid.uuid4().hex[:6]}"
    print(f"user_id={user_id}")
    print(f"session_a={session_a}  session_b={session_b}\n")

    session_service = KurrentDBSessionService(kdb)
    memory_service = KurrentDBMemoryService(kdb)
    artifact_service = KurrentDBArtifactService(kdb)

    def make_runner() -> Runner:
        return Runner(
            app=App(name=APP_NAME, root_agent=root_agent),
            session_service=session_service,
            memory_service=memory_service,
            artifact_service=artifact_service,
        )

    # --- Session A ------------------------------------------------------------
    print("=== Session A — building up state ===")
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_a
    )
    runner_a = make_runner()

    turns_a = [
        (
            "Here are my notes on event sourcing: 'Event sourcing stores state "
            "as an immutable log of domain events; the current state is "
            "derived by replaying them. It gives you full audit trail by "
            "construction.' Save it as 'event-sourcing'."
        ),
        (
            "And on KurrentDB: 'KurrentDB is an event store designed for "
            "event sourcing — per-stream ordering, optimistic concurrency, "
            "server-side projections.' Save as 'kurrentdb'."
        ),
        (
            "Update event-sourcing with this addition: 'Snapshots speed up "
            "replay for long-lived streams.'"
        ),
        "What notes do I have?",
    ]
    for message in turns_a:
        print(f"User:  {message}")
        answer = await ask(
            runner_a, user_id=user_id, session_id=session_a, message=message
        )
        print(f"Agent: {answer}\n")

    # --- Direct inspection ----------------------------------------------------
    print("=== Direct state check (bypassing the agent) ===")
    mem = await memory_service.search_memory(
        app_name=APP_NAME, user_id=user_id, query=""
    )
    print(f"Memory entries retained ({len(mem.memories)}):")
    for entry in mem.memories:
        text = entry.content.parts[0].text if entry.content.parts else ""
        print(f"  • {text}")

    note_names = await artifact_service.list_artifact_keys(
        app_name=APP_NAME, user_id=user_id
    )
    print(f"\nArtifact filenames (user-scoped): {note_names}")
    for name in note_names:
        versions = await artifact_service.list_versions(
            app_name=APP_NAME, user_id=user_id, filename=name
        )
        print(f"  • {name}: versions={versions}")
    print()

    # --- Session B: different session_id, no shared history ------------------
    print("=== Session B — new session, same user, no shared chat history ===")
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_b
    )
    runner_b = make_runner()

    turns_b = [
        "What have I been researching lately?",
        "Show me my event-sourcing notes.",
    ]
    for message in turns_b:
        print(f"User:  {message}")
        answer = await ask(
            runner_b, user_id=user_id, session_id=session_b, message=message
        )
        print(f"Agent: {answer}\n")

    await kdb.close()


if __name__ == "__main__":
    asyncio.run(main())
