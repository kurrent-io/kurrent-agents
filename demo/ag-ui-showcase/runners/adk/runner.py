"""Google ADK lane runner.

DUMMY_MODE=1 — write canned canonical events via the shared helper.
Otherwise runs ADK properly (Phase 2.5 — needs ANTHROPIC_API_KEY or
GOOGLE_GENAI_USE_VERTEXAI).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared.dummy import write_dummy_session  # noqa: E402

from kurrentdbclient import AsyncKurrentDBClient  # noqa: E402

CONN = os.environ.get(
    "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
)
DUMMY_MODE = os.environ.get("DUMMY_MODE", "").lower() in ("1", "true", "yes")


async def main() -> int:
    print(f"[adk] starting; DUMMY_MODE={DUMMY_MODE}", file=sys.stderr, flush=True)
    payload = json.loads(sys.stdin.read())
    session_id = payload["session_id"]
    user_message = payload["user_message"]

    client = AsyncKurrentDBClient(CONN)
    try:
        if DUMMY_MODE:
            await write_dummy_session(
                client,
                session_id=session_id,
                user_message=user_message,
                app_name="ag-ui-showcase",
                agent_name="ADK Weather Agent",
                model="gemini-2.5-flash (dummy)",
            )
        else:
            raise NotImplementedError("Real ADK lane needs LLM credentials (Phase 2.5).")
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
