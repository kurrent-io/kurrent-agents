"""Cross-language acceptance tests for the AG-UI → KurrentDB middleware.

Spawns the TypeScript middleware writer (`npm run interop:write`) which
runs a synthetic AG-UI agent through `KurrentDBMiddleware`, then verifies
the resulting canonical stream from two angles:

1. **Schema-level read** — parse each event with `kurrent-agent-schema`
   and assert the proto types and field values. Proves the wire format
   matches what every Python and .NET integration expects.

2. **Real-integration read** — feed the same stream through MAF Python's
   `KurrentDBHistoryProvider.get_messages()`, the same code path MAF
   uses in production. Proves the middleware-written stream is wire-
   compatible with an existing first-party Kurrent integration's reader.

Together they cover the DEV-1558 acceptance criterion: "wrapping any
existing /integrations/* agent with this middleware produces a
replayable AgentSession-* stream readable by kurrent-agents' existing
Python/.NET readers."

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


@pytest.mark.asyncio
async def test_maf_history_provider_reads_canonical_events_written_by_ts_middleware(
    client: AsyncKurrentDBClient,
    write_session_via_middleware,
) -> None:
    """The full DEV-1558 acceptance: an existing first-party Kurrent
    integration's reader reconstructs Message objects from the canonical
    stream the TS middleware wrote.

    This is the strongest possible test without an LLM in the loop.
    The middleware doesn't know MAF exists; MAF doesn't know the
    middleware exists; the canonical schema is the only contract
    they share — and it works.
    """
    from agent_framework import Message
    from kurrent_agent_framework import KurrentDBHistoryProvider

    session_id = f"interop-maf-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    write_session_via_middleware(session_id, run_id)

    history = KurrentDBHistoryProvider(
        client,
        source_id="interop",
        agent_name="fake",
        model_name="synthetic",
    )
    messages: list[Message] = await history.get_messages(session_id)

    # The synthetic AG-UI agent emits 4 conversational messages: user,
    # assistant-with-tool-call, tool-result, assistant-text. MAF's reader
    # should reconstruct exactly that.
    roles = [m.role.value if hasattr(m.role, "value") else m.role for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"], (
        f"unexpected role sequence: {roles}"
    )

    # User message reconstructed with text content.
    assert messages[0].text == "Weather in Oslo?"

    # Assistant message with a function_call content block.
    assistant_with_call = messages[1]
    function_calls = [
        c for c in assistant_with_call.contents if c.type == "function_call"
    ]
    assert len(function_calls) == 1
    fc = function_calls[0]
    assert fc.name == "get_weather"
    assert fc.call_id == "c1"
    # MAF FunctionCallContent stores arguments as the canonical Struct
    # round-tripped to a dict.
    assert dict(fc.arguments) == {"city": "Oslo"}
    # Carrier text from the assistant message is preserved alongside the call.
    text_blocks = [c for c in assistant_with_call.contents if c.type == "text"]
    assert any(c.text == "Looking up." for c in text_blocks), (
        f"missing carrier text in {[c.type for c in assistant_with_call.contents]}"
    )

    # Tool result message.
    tool_result_msg = messages[2]
    fr = next(
        c for c in tool_result_msg.contents if c.type == "function_result"
    )
    assert fr.call_id == "c1"
    # MAF stores function_result.result as a string — same as the canonical
    # ToolResultReceived.result field.
    assert json.loads(fr.result) == {
        "temperature_c": 8,
        "condition": "light_rain",
    }

    # Final assistant text.
    assert messages[3].text == "8°C with light rain."


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
