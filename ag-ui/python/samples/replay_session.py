"""Minimal demo: write a canonical session, replay it as AG-UI events.

Mirrors ``microsoft-agent-framework/python/samples/basic_roundtrip.py`` in
shape, but the read-back step shows the *AG-UI* event stream rather than
canonical events — the point of the bridge.

Prereq: a KurrentDB instance at ``kurrentdb://localhost:2113?Tls=false``
(use ``demo/docker-compose.yml`` from the repo root).

Run::

    cd ag-ui/python
    uv run python samples/replay_session.py

Then optionally tail the same session over SSE in another terminal::

    uv run uvicorn kurrent_ag_ui.server:app --port 8000
    curl -N "http://localhost:8000/sessions/{the-printed-id}/events"
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

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

from kurrent_ag_ui.bridge import stream_session_events

CONN = "kurrentdb://localhost:2113?Tls=false"


def _now() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return ts


def _struct(d: dict) -> Struct:
    s = Struct()
    s.update(d)
    return s


def _new_event(msg) -> NewEvent:
    return NewEvent(
        id=uuid.uuid4(),
        type=EVENT_TYPE_NAMES[type(msg)],
        data=to_json(msg).encode("utf-8"),
        metadata=b"{}",
    )


async def main() -> None:
    session_id = f"sample-{uuid.uuid4().hex[:8]}"
    client = AsyncKurrentDBClient(CONN)
    await client.connect()

    try:
        # --- write a synthetic two-turn session ---
        await client.append_to_stream(
            agent_session_stream(session_id),
            events=[
                _new_event(
                    SessionStarted(
                        app_name="weather_demo",
                        user_id="alice",
                        model="claude-haiku-4-5",
                        timestamp=_now(),
                    )
                ),
                _new_event(
                    UserMessageReceived(
                        content="What's the weather in Tokyo?",
                        message_id="u1",
                        author_name="alice",
                        created_at=_now(),
                        timestamp=_now(),
                    )
                ),
                _new_event(
                    AssistantToolCallsGenerated(
                        tool_calls=[
                            ToolCallInfo(
                                call_id="call-tokyo",
                                tool_name="get_weather",
                                arguments=_struct({"city": "Tokyo"}),
                            )
                        ],
                        content="Let me check.",
                        message_id="a1",
                        author_name="root",
                        created_at=_now(),
                        timestamp=_now(),
                    )
                ),
                _new_event(
                    ToolResultReceived(
                        call_id="call-tokyo",
                        tool_name="get_weather",
                        result=json.dumps({"temperature_c": 22, "conditions": "sunny"}),
                        message_id="t1",
                        created_at=_now(),
                        timestamp=_now(),
                    )
                ),
                _new_event(
                    AssistantTextGenerated(
                        content="It's sunny and 22°C in Tokyo.",
                        message_id="a2",
                        author_name="root",
                        created_at=_now(),
                        timestamp=_now(),
                    )
                ),
                _new_event(SessionEnded(reason="complete", timestamp=_now())),
            ],
            current_version=StreamState.NO_STREAM,
        )

        print(f"=== Wrote session {session_id} ===\n")

        # --- replay it as AG-UI events ---
        print("=== AG-UI event stream ===\n")
        async for ev in stream_session_events(client, session_id):
            etype = ev["type"]
            # Friendlier inline summary per type.
            if etype in ("RUN_STARTED", "RUN_FINISHED"):
                print(f"  {etype}  thread={ev['threadId']} run={ev['runId']}")
            elif etype == "TEXT_MESSAGE_START":
                print(f"  {etype}  ({ev['role']:9}) id={ev['messageId']}")
            elif etype == "TEXT_MESSAGE_CONTENT":
                print(f"  {etype}  delta={ev['delta']!r}")
            elif etype == "TEXT_MESSAGE_END":
                print(f"  {etype}    id={ev['messageId']}")
            elif etype == "TOOL_CALL_START":
                print(
                    f"  {etype}     id={ev['toolCallId']} name={ev['toolCallName']}"
                    f" parent={ev.get('parentMessageId')}"
                )
            elif etype == "TOOL_CALL_ARGS":
                print(f"  {etype}      id={ev['toolCallId']} delta={ev['delta']}")
            elif etype == "TOOL_CALL_END":
                print(f"  {etype}       id={ev['toolCallId']}")
            elif etype == "TOOL_CALL_RESULT":
                print(
                    f"  {etype}    id={ev['toolCallId']} content={ev['content']}"
                )
            else:
                print(f"  {etype}  {ev}")

        print(f"\n=== Done. Try: curl -N http://localhost:8000/sessions/{session_id}/events ===")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
