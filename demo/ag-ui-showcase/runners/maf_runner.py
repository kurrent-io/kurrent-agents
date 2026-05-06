"""MAF Python runner for the ag-ui-showcase demo.

Reads from stdin a single JSON line: ``{"session_id": ..., "user_message": ...}``.
Runs the agent, writes canonical events to KurrentDB via MAF Python's
``KurrentDBHistoryProvider``. Exits when the turn is complete.

Two modes:

* **DUMMY_MODE=1** — no LLM call. Writes a canned canonical event sequence
  directly via ``kurrent-agent-schema`` so the server can verify the
  end-to-end plumbing without an API key.
* Otherwise — runs MAF properly, uses ``ANTHROPIC_API_KEY``. (Phase 2.)

Env:
- ``KURRENTDB_CONNECTION_STRING`` (default ``kurrentdb://localhost:2113?Tls=false``)
- ``DUMMY_MODE`` — when truthy, skip the real LLM
- ``ANTHROPIC_API_KEY`` — when not in dummy mode
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    SessionEnded,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    agent_session_stream,
    to_json,
)
from kurrent_agent_schema.registry import EVENT_TYPE_NAMES
from kurrentdbclient import AsyncKurrentDBClient, NewEvent, StreamState

CONN = os.environ.get(
    "KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false"
)
DUMMY_MODE = os.environ.get("DUMMY_MODE", "").lower() in ("1", "true", "yes")


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return ts


def _struct(d: dict[str, Any]) -> Struct:
    s = Struct()
    s.update(d)
    return s


def _new_event(msg: Any, *, run_id: str) -> NewEvent:
    return NewEvent(
        id=uuid.uuid4(),
        type=EVENT_TYPE_NAMES[type(msg)],
        data=to_json(msg).encode("utf-8"),
        metadata=json.dumps({"$schema_version": 2, "$run_id": run_id}).encode("utf-8"),
    )


async def _dummy_run(client: AsyncKurrentDBClient, session_id: str, user_message: str) -> None:
    """Write a canned conversation as canonical events.

    Sequence: SessionStarted (idempotent — only on new session) →
    UserMessageReceived → AssistantToolCallsGenerated (carrier text +
    weather lookup) → ToolResultReceived → AssistantTextGenerated → SessionEnded.

    Each canonical event becomes a single AG-UI message-grained chunk
    when the server replays it via DEV-1559 in live mode.
    """
    stream = agent_session_stream(session_id)
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    # Decide if this is the first turn for the session.
    first_turn = True
    try:
        existing = await client.read_stream(stream)
        async for _ in existing:
            first_turn = False
            break
    except Exception:
        pass

    events: list[NewEvent] = []
    if first_turn:
        events.append(
            _new_event(
                SessionStarted(
                    app_name="ag-ui-showcase",
                    agent_name="WeatherAgent",
                    model="claude-haiku-4-5 (dummy)",
                    timestamp=_now_ts(),
                ),
                run_id=run_id,
            )
        )

    user_msg_id = f"u-{uuid.uuid4().hex[:6]}"
    events.append(
        _new_event(
            UserMessageReceived(
                content=user_message,
                message_id=user_msg_id,
                author_name="user",
                created_at=_now_ts(),
                timestamp=_now_ts(),
            ),
            run_id=run_id,
        )
    )

    # Canned response shaped around a "weather" theme so the demo has
    # tool-call content. Real MAF lane lands in Phase 2.
    asst1_id = f"a-{uuid.uuid4().hex[:6]}"
    call_id = f"c-{uuid.uuid4().hex[:6]}"
    events.append(
        _new_event(
            AssistantToolCallsGenerated(
                tool_calls=[
                    ToolCallInfo(
                        call_id=call_id,
                        tool_name="get_weather",
                        arguments=_struct({"query": user_message}),
                    )
                ],
                content="Let me look that up for you.",
                message_id=asst1_id,
                author_name="WeatherAgent",
                created_at=_now_ts(),
                timestamp=_now_ts(),
            ),
            run_id=run_id,
        )
    )

    tool_msg_id = f"t-{uuid.uuid4().hex[:6]}"
    events.append(
        _new_event(
            ToolResultReceived(
                call_id=call_id,
                tool_name="get_weather",
                result=json.dumps(
                    {"query": user_message, "temperature_c": 22, "condition": "sunny"}
                ),
                message_id=tool_msg_id,
                created_at=_now_ts(),
                timestamp=_now_ts(),
            ),
            run_id=run_id,
        )
    )

    asst2_id = f"a-{uuid.uuid4().hex[:6]}"
    canned_reply = (
        f'(dummy mode reply) You asked "{user_message}". '
        f"It's sunny and 22°C wherever you are."
    )
    events.append(
        _new_event(
            AssistantTextGenerated(
                content=canned_reply,
                message_id=asst2_id,
                author_name="WeatherAgent",
                created_at=_now_ts(),
                timestamp=_now_ts(),
            ),
            run_id=run_id,
        )
    )

    events.append(
        _new_event(SessionEnded(reason="complete", timestamp=_now_ts()), run_id=run_id)
    )

    # Append events one at a time with small async sleeps so the live-tail
    # subscriber on the server side has visible streaming latency. In a
    # real LLM run this latency comes naturally from token generation.
    for e in events:
        await client.append_to_stream(stream, events=[e], current_version=StreamState.ANY)
        await asyncio.sleep(0.25)


async def _real_run(client: AsyncKurrentDBClient, session_id: str, user_message: str) -> None:
    """Real MAF agent run. Phase 2."""
    raise NotImplementedError(
        "Real MAF lane lands in Phase 2. Set DUMMY_MODE=1 for now."
    )


async def main() -> int:
    print(f"[maf_runner] starting; DUMMY_MODE={DUMMY_MODE}; CONN={CONN}", file=sys.stderr, flush=True)
    raw = sys.stdin.read()
    print(f"[maf_runner] stdin read {len(raw)} bytes", file=sys.stderr, flush=True)
    payload = json.loads(raw)
    session_id = payload["session_id"]
    user_message = payload["user_message"]
    print(f"[maf_runner] session_id={session_id} message={user_message[:50]!r}", file=sys.stderr, flush=True)

    client = AsyncKurrentDBClient(CONN)
    try:
        if DUMMY_MODE:
            await _dummy_run(client, session_id, user_message)
        else:
            await _real_run(client, session_id, user_message)
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
