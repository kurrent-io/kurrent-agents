"""Cross-language acceptance test for the AG-UI → KurrentDB middleware.

Spawns the TypeScript middleware writer (`npm run interop:write`) which
runs a synthetic AG-UI agent, the middleware persists the canonical
events, and this test reads the resulting `AgentSession-{id}` stream
via the Python `kurrent-agent-schema` package — the same package every
other Python integration in this monorepo uses.

If this test passes, the wire format produced by the TypeScript
middleware is structurally identical to what Python and .NET readers
expect. That's the DEV-1558 acceptance criterion.

Prereqs:
  - KurrentDB running (`demo/docker-compose up -d`).
  - Middleware deps installed (`cd ../middlewares/kurrentdb-middleware && npm install`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from google.protobuf.message import Message
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    SessionEnded,
    SessionStarted,
    ToolResultReceived,
    UserMessageReceived,
    agent_session_stream,
    from_json,
)
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME
from kurrentdbclient import AsyncKurrentDBClient

CONN = os.environ.get(
    "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
)

MIDDLEWARE_DIR = Path(__file__).resolve().parent.parent / "middlewares" / "kurrentdb-middleware"


def _resolve_npm() -> str:
    """Locate npm; on Windows it's npm.cmd."""
    for candidate in ("npm.cmd", "npm"):
        path = shutil.which(candidate)
        if path:
            return path
    raise RuntimeError("npm not found on PATH")


@pytest_asyncio.fixture
async def client():
    c = AsyncKurrentDBClient(CONN)
    try:
        yield c
    finally:
        try:
            await c.close()
        except Exception:
            pass


@pytest.fixture
def write_session_via_middleware():
    """Returns a function `(session_id, run_id?) -> dict` that runs the TS
    writer and returns the {sessionId, runId} record it prints."""

    def _run(session_id: str, run_id: str | None = None) -> dict[str, str]:
        npm = _resolve_npm()
        args = [npm, "run", "--silent", "interop:write", "--", session_id]
        if run_id:
            args.append(run_id)
        env = {**os.environ, "KURRENTDB_CONNECTION_STRING": CONN}
        result = subprocess.run(
            args,
            cwd=MIDDLEWARE_DIR,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # The writer logs a single JSON line on stdout when it finishes.
        lines = [
            line for line in result.stdout.splitlines() if line.strip().startswith("{")
        ]
        assert lines, f"no JSON marker in writer stdout:\n{result.stdout}\n---STDERR---\n{result.stderr}"
        return json.loads(lines[-1])

    return _run


@pytest.mark.asyncio
async def test_python_reads_canonical_events_written_by_ts_middleware(
    client: AsyncKurrentDBClient,
    write_session_via_middleware,
) -> None:
    session_id = f"interop-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    marker = write_session_via_middleware(session_id, run_id)
    assert marker == {"sessionId": session_id, "runId": run_id}

    # Read the AgentSession stream via Python's canonical schema package.
    # Each event type round-trips through `kurrent_agent_schema.from_json`
    # to prove the wire bytes parse cleanly into the generated proto types.
    events: list[tuple[str, Message, dict[str, Any]]] = []
    recorded = await client.read_stream(agent_session_stream(session_id))
    async for record in recorded:
        msg_cls = EVENT_TYPE_BY_NAME[record.type]
        msg = from_json(msg_cls, record.data.decode("utf-8"))
        meta = json.loads(record.metadata.decode("utf-8")) if record.metadata else {}
        events.append((record.type, msg, meta))

    types = [t for t, _, _ in events]
    assert types == [
        "SessionStarted",
        "UserMessageReceived",
        "AssistantToolCallsGenerated",
        "ToolResultReceived",
        "AssistantTextGenerated",
        "SessionEnded",
    ], f"unexpected sequence: {types}"

    # Spot-check the parsed messages — every event type the TS middleware
    # emitted in v1 should deserialise cleanly into its proto class.
    session_started = events[0][1]
    assert isinstance(session_started, SessionStarted)
    assert session_started.app_name == "interop"
    assert session_started.agent_name == "fake"

    user_msg = events[1][1]
    assert isinstance(user_msg, UserMessageReceived)
    assert user_msg.content == "Weather in Oslo?"
    assert user_msg.message_id == "u1"

    tool_calls = events[2][1]
    assert isinstance(tool_calls, AssistantToolCallsGenerated)
    assert tool_calls.message_id == "a1"
    assert tool_calls.content == "Looking up."
    assert len(tool_calls.tool_calls) == 1
    call = tool_calls.tool_calls[0]
    assert call.call_id == "c1"
    assert call.tool_name == "get_weather"
    # arguments is google.protobuf.Struct; round-trip through dict.
    args = dict(call.arguments)
    assert args == {"city": "Oslo"}

    tool_result = events[3][1]
    assert isinstance(tool_result, ToolResultReceived)
    assert tool_result.call_id == "c1"
    parsed_result = json.loads(tool_result.result)
    assert parsed_result == {"temperature_c": 8, "condition": "light_rain"}

    assistant_text = events[4][1]
    assert isinstance(assistant_text, AssistantTextGenerated)
    assert assistant_text.content == "8°C with light rain."

    session_ended = events[5][1]
    assert isinstance(session_ended, SessionEnded)
    assert session_ended.reason == "complete"

    # runId stamped on every event's metadata.
    for t, _, meta in events:
        assert meta.get("$run_id") == run_id, f"missing $run_id on {t}"
        assert meta.get("$schema_version") == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
