"""End-to-end test: write canonical events to a real KurrentDB, read them
back through the bridge, and assert the AG-UI sequence.

Connects to a KurrentDB instance at
``KURRENTDB_CONNECTION_STRING`` (default ``kurrentdb://localhost:2113?Tls=false``).
Requires ``demo/docker-compose up -d`` to be running.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
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
from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.struct_pb2 import Struct

from kurrent_ag_ui.bridge import stream_session_events

CONN = os.environ.get("KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false")


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


def _now() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return ts


def _struct(d: dict[str, Any]) -> Struct:
    s = Struct()
    s.update(d)
    return s


def _new_event(msg: Any) -> NewEvent:
    type_name = EVENT_TYPE_NAMES[type(msg)]
    return NewEvent(
        id=uuid.uuid4(),
        type=type_name,
        data=to_json(msg).encode("utf-8"),
        metadata=b"{}",
    )


@pytest.mark.asyncio
async def test_round_trip_canonical_to_ag_ui(client: AsyncKurrentDBClient):
    session_id = f"agui-test-{uuid.uuid4().hex[:8]}"
    stream = agent_session_stream(session_id)

    events_to_write = [
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
    ]

    await client.append_to_stream(
        stream,
        events=events_to_write,
        current_version=StreamState.NO_STREAM,
    )

    collected = [ev async for ev in stream_session_events(client, session_id)]
    types = [ev["type"] for ev in collected]
    assert types == [
        "RUN_STARTED",
        # user message
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        # assistant carrier text + tool call
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        # final assistant text
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]

    # Spot-check a couple of payloads.
    run_started = collected[0]
    assert run_started["threadId"] == session_id
    assert run_started["runId"] == session_id

    tool_call_start = next(e for e in collected if e["type"] == "TOOL_CALL_START")
    assert tool_call_start["toolCallId"] == "call-tokyo"
    assert tool_call_start["toolCallName"] == "get_weather"
    assert tool_call_start["parentMessageId"] == "a1"

    tool_call_args = next(e for e in collected if e["type"] == "TOOL_CALL_ARGS")
    assert json.loads(tool_call_args["delta"]) == {"city": "Tokyo"}

    tool_result = next(e for e in collected if e["type"] == "TOOL_CALL_RESULT")
    assert tool_result["toolCallId"] == "call-tokyo"
    assert json.loads(tool_result["content"]) == {
        "temperature_c": 22,
        "conditions": "sunny",
    }
