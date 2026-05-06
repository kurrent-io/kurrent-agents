"""Strands lane runner.

DUMMY_MODE=1 — write canned canonical events.
Otherwise runs a real Strands ``Agent`` against Anthropic Claude;
``KurrentDBSessionManager`` persists canonical events as the agent
runs.

Strands is sync-native (``KurrentDBSessionManager`` uses the sync
``KurrentDBClient``), so the real path is sync; the dummy path is
async, run via ``asyncio.run`` from the sync entry point.
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

CONN = os.environ.get(
    "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
)
DUMMY_MODE = os.environ.get("DUMMY_MODE", "").lower() in ("1", "true", "yes")
APP_NAME = "ag-ui-showcase"
USER_ID = "showcase-user"
AGENT_NAME = "Strands_Weather_Agent"


async def _dummy(session_id: str, user_message: str) -> None:
    from kurrentdbclient import AsyncKurrentDBClient

    client = AsyncKurrentDBClient(CONN)
    try:
        await write_dummy_session(
            client,
            session_id=session_id,
            user_message=user_message,
            app_name=APP_NAME,
            agent_name="Strands Weather Agent",
            model="claude-haiku-4-5 (dummy)",
        )
    finally:
        try:
            await client.close()
        except Exception:
            pass


def _real(session_id: str, user_message: str) -> None:
    """Run one turn of a real Strands agent. Sync."""
    from strands import Agent, tool
    from strands.models.anthropic import AnthropicModel

    from kurrent_strands import KurrentDBSessionManager, client as kdb_client

    @tool
    def get_weather(city: str) -> dict:
        """Look up current weather for a city.

        Args:
            city: The city name.
        """
        return lookup_weather(city)

    kdb = kdb_client.from_connection_string(CONN)
    manager = KurrentDBSessionManager(
        client=kdb,
        session_id=session_id,
        app_name=APP_NAME,
        user_id=USER_ID,
        agent_name=AGENT_NAME,
    )
    agent = Agent(
        agent_id=AGENT_NAME,
        name=AGENT_NAME,
        model=AnthropicModel(model_id=MODEL_ID, max_tokens=1024),
        system_prompt=SYSTEM_PROMPT,
        tools=[get_weather],
        session_manager=manager,
    )
    result = agent(user_message)
    text = ""
    for block in result.message.get("content") or []:
        if block.get("text"):
            text = block["text"].strip()
            break
    print(f"[strands] response: {text[:120]}", file=sys.stderr, flush=True)


def main() -> int:
    print(f"[strands] starting; DUMMY_MODE={DUMMY_MODE}", file=sys.stderr, flush=True)
    payload = json.loads(sys.stdin.read())
    session_id = payload["session_id"]
    user_message = payload["user_message"]

    if DUMMY_MODE:
        asyncio.run(_dummy(session_id, user_message))
    else:
        _real(session_id, user_message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
