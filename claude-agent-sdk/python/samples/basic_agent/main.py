"""End-to-end demo of ``KurrentDBSessionStore``.

Two turns sharing one ``session_id``. Turn 1 runs against a fresh session;
the Claude CLI writes its local JSONL transcript, and our ``SessionStore``
adapter mirrors every line to KurrentDB. Turn 2 resumes the session via
``ClaudeAgentOptions.resume``; the SDK calls ``store.load(key)``, we
reconstruct the transcript from KurrentDB, and the CLI materialises it into
a temp JSONL file to resume from.

This is the only integration in the monorepo where the Python layer **does
not own conversation state** — the CLI does. See DESIGN.md §2.

Prerequisites:
    - KurrentDB running:       docker compose up -d
    - Claude CLI installed:    https://docs.claude.com/en/docs/claude-code/quickstart
    - Claude CLI authenticated: `claude login` or `ANTHROPIC_API_KEY` in env

Run:
    python -m samples.basic_agent.main          # from claude-agent-sdk/python/
"""

from __future__ import annotations

import asyncio
import os
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from kurrent_agent_schema.streams import agent_session_stream

from kurrent_claude_agent_sdk import KurrentDBSessionStore
from kurrent_claude_agent_sdk import client as kdb_client

APP_NAME = "basic_claude_sdk_demo"
USER_ID = "alice"


async def run_turn(prompt: str, options: ClaudeAgentOptions) -> str:
    """Run one turn; return the concatenated final assistant text."""
    parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "".join(parts).strip()


async def main() -> None:
    conn = os.environ.get(
        "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
    )
    kdb = kdb_client.from_connection_string(conn)
    store = KurrentDBSessionStore(kdb, app_name=APP_NAME, user_id=USER_ID)

    session_id = str(uuid.uuid4())
    print(f"Session id: {session_id}\n")

    # --- Turn 1: fresh session ------------------------------------------------
    print("=== Turn 1 (fresh session) ===")
    question_1 = (
        "Hi! My name is Alexey, I work at Kurrent building an event-sourced "
        "agent platform. What's 2+2?"
    )
    print(f"User:  {question_1}")
    options_1 = ClaudeAgentOptions(
        session_store=store,
        session_id=session_id,
        # No tools — keep the sample simple. The SDK supports tool use but
        # that requires allowlisting Read/Bash/etc. which would obscure the
        # persistence story.
        allowed_tools=[],
        max_turns=1,
    )
    answer_1 = await run_turn(question_1, options_1)
    print(f"Claude: {answer_1}\n")

    # --- Turn 2: resume the session from KurrentDB ---------------------------
    # ``resume=session_id`` triggers ``store.load(key)`` — our adapter
    # reconstructs the CLI's JSONL transcript from the KurrentDB stream and
    # the SDK materialises it into a temp file for the subprocess.
    print("=== Turn 2 (resumed session via KurrentDBSessionStore.load) ===")
    question_2 = "What did I tell you my name was, and where do I work?"
    print(f"User:  {question_2}")
    options_2 = ClaudeAgentOptions(
        session_store=store,
        resume=session_id,
        allowed_tools=[],
        max_turns=1,
    )
    answer_2 = await run_turn(question_2, options_2)
    print(f"Claude: {answer_2}\n")

    # --- Persisted stream dump -----------------------------------------------
    print("=== Persisted events in KurrentDB ===")
    stream = agent_session_stream(session_id)
    records = await kdb.get_stream(stream)
    print(f"Total events on {stream}: {len(records)}")
    type_counts: dict[str, int] = {}
    for r in records:
        type_counts[r.type] = type_counts.get(r.type, 0) + 1
    for event_type, count in type_counts.items():
        print(f"  {event_type}: {count}")

    await kdb.close()


if __name__ == "__main__":
    asyncio.run(main())
