"""Google ADK lane runner.

DUMMY_MODE=1 — write canned canonical events.
Otherwise runs a real ADK ``Agent`` via ``LiteLlm`` against Anthropic
Claude. ``KurrentDBSessionService`` persists canonical events as the
agent runs.
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
APP_NAME = "ag-ui-showcase"
USER_ID = "showcase-user"
AGENT_NAME = "ADK_Weather_Agent"


def get_weather(city: str) -> dict:
    """Look up current weather for a city.

    Args:
        city: The city name.
    """
    return lookup_weather(city)


async def _dummy(client: AsyncKurrentDBClient, session_id: str, user_message: str) -> None:
    await write_dummy_session(
        client,
        session_id=session_id,
        user_message=user_message,
        app_name=APP_NAME,
        agent_name="ADK Weather Agent",
        model="claude-haiku-4-5 via LiteLLM (dummy)",
    )


async def _real(session_id: str, user_message: str) -> None:
    """Run one turn of a real ADK agent. The KurrentDBSessionService
    handles session create-or-resume; the runner persists each event as
    the turn progresses."""
    from google.adk import Agent
    from google.adk.apps import App
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.genai import types

    from kurrent_google_adk import KurrentDBSessionService, client as kdb_client

    kdb = kdb_client.from_connection_string(CONN)
    session_service = KurrentDBSessionService(kdb)
    try:
        # Try to get an existing session — if not present, create.
        existing = None
        try:
            existing = await session_service.get_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id
            )
        except Exception:
            existing = None
        if existing is None:
            await session_service.create_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id
            )

        agent = Agent(
            name=AGENT_NAME,
            model=LiteLlm(model=f"anthropic/{MODEL_ID}"),
            description="Helpful assistant that can look up the weather.",
            instruction=SYSTEM_PROMPT,
            tools=[get_weather],
        )
        runner = Runner(
            app=App(name=APP_NAME, root_agent=agent), session_service=session_service
        )
        content = types.Content(role="user", parts=[types.Part(text=user_message)])
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final = "".join(p.text or "" for p in event.content.parts).strip()
                print(f"[adk] response: {final[:120]}", file=sys.stderr, flush=True)
    finally:
        try:
            await kdb.close()
        except Exception:
            pass


async def main() -> int:
    print(f"[adk] starting; DUMMY_MODE={DUMMY_MODE}", file=sys.stderr, flush=True)
    payload = json.loads(sys.stdin.read())
    session_id = payload["session_id"]
    user_message = payload["user_message"]

    # ADK integration fallback: ``kurrent_google_adk`` currently
    # imports ``kurrent_agent_schema.events`` which moved when schema
    # 0.4.0 went proto-generated. Until the integration is migrated,
    # the ADK lane falls back to dummy mode even when DUMMY_MODE is
    # unset. Tracked separately from the demo PR.
    use_dummy = DUMMY_MODE
    if not use_dummy:
        try:
            import kurrent_google_adk  # noqa: F401
        except ModuleNotFoundError as exc:
            if "kurrent_agent_schema.events" in str(exc):
                print(
                    "[adk] WARNING: kurrent_google_adk import is broken against schema 0.4.0; "
                    "falling back to dummy mode for the ADK lane.",
                    file=sys.stderr, flush=True,
                )
                use_dummy = True
            else:
                raise

    if use_dummy:
        client = AsyncKurrentDBClient(CONN)
        try:
            await _dummy(client, session_id, user_message)
        finally:
            try:
                await client.close()
            except Exception:
                pass
    else:
        await _real(session_id, user_message)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
