"""MAF lane runner.

DUMMY_MODE=1 — write canned canonical events via the shared helper.
Otherwise runs a real MAF agent using ``KurrentDBHistoryProvider`` +
``UsageCapture``. The provider auto-saves canonical events to KurrentDB
each turn — the showcase server's DEV-1559 live-tail picks them up and
streams them back to the browser.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared.dummy import write_dummy_session  # noqa: E402
from _shared.weather import MODEL_ID, SYSTEM_PROMPT, lookup_weather  # noqa: E402

from kurrentdbclient import AsyncKurrentDBClient  # noqa: E402

CONN = os.environ.get(
    "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
)
DUMMY_MODE = os.environ.get("DUMMY_MODE", "").lower() in ("1", "true", "yes")


def get_weather(city: str) -> dict:
    """Look up current weather for a city."""
    return lookup_weather(city)


async def _dummy(client: AsyncKurrentDBClient, session_id: str, user_message: str) -> None:
    await write_dummy_session(
        client,
        session_id=session_id,
        user_message=user_message,
        app_name="ag-ui-showcase",
        agent_name="MAF Weather Agent",
        model="claude-haiku-4-5 (dummy)",
    )


async def _real(client: AsyncKurrentDBClient, session_id: str, user_message: str) -> None:
    """Run one turn of a real MAF agent against Anthropic Claude.

    KurrentDBHistoryProvider auto-loads any prior history for this
    session_id and saves the new turn's messages back to KurrentDB.
    """
    from agent_framework import Agent, AgentSession
    from agent_framework.anthropic import AnthropicClient
    from kurrent_agent_framework import KurrentDBHistoryProvider, UsageCapture

    usage = UsageCapture()
    history = KurrentDBHistoryProvider(
        client,
        source_id="kurrentdb_history",
        app_name="ag-ui-showcase",
        agent_name="MAF Weather Agent",
        model_name=MODEL_ID,
        usage_capture=usage,
    )
    chat = AnthropicClient(model=MODEL_ID)
    agent = Agent(
        chat,
        instructions=SYSTEM_PROMPT,
        name="MAF Weather Agent",
        tools=[get_weather],
        context_providers=[history],
        middleware=[usage],
    )
    session = AgentSession(session_id=session_id)
    response = await agent.run(user_message, session=session)
    print(f"[maf] response: {response.text.strip()[:120]}", file=sys.stderr, flush=True)


async def main() -> int:
    print(f"[maf] starting; DUMMY_MODE={DUMMY_MODE}", file=sys.stderr, flush=True)
    payload = json.loads(sys.stdin.read())
    session_id = payload["session_id"]
    user_message = payload["user_message"]

    client = AsyncKurrentDBClient(CONN)
    try:
        if DUMMY_MODE:
            await _dummy(client, session_id, user_message)
        else:
            await _real(client, session_id, user_message)
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
