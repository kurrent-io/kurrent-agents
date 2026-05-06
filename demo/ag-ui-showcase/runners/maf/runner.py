"""MAF lane runner.

DUMMY_MODE=1 — write canned canonical events via the shared helper.
Otherwise runs MAF properly (Phase 2.5 — needs ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Each lane runs in its own venv (independent dependency footprints).
# `_shared` is a sibling module dir — add its parent to sys.path so the
# import works regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared.dummy import write_dummy_session  # noqa: E402

from kurrentdbclient import AsyncKurrentDBClient  # noqa: E402

CONN = os.environ.get(
    "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
)
DUMMY_MODE = os.environ.get("DUMMY_MODE", "").lower() in ("1", "true", "yes")


async def main() -> int:
    print(f"[maf] starting; DUMMY_MODE={DUMMY_MODE}", file=sys.stderr, flush=True)
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
                agent_name="MAF Weather Agent",
                model="claude-haiku-4-5 (dummy)",
            )
        else:
            raise NotImplementedError("Real MAF lane needs ANTHROPIC_API_KEY (Phase 2.5).")
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
