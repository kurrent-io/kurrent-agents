"""SSE server smoke test.

Writes a short canonical session to KurrentDB, mounts the Starlette app
in-process via httpx ASGI transport, and parses the SSE stream.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from kurrent_agent_schema import (
    AssistantTextGenerated,
    SessionEnded,
    SessionStarted,
    UserMessageReceived,
    agent_session_stream,
    to_json,
)
from kurrent_agent_schema.registry import EVENT_TYPE_NAMES
from kurrentdbclient import AsyncKurrentDBClient, NewEvent, StreamState
from google.protobuf.timestamp_pb2 import Timestamp

from kurrent_ag_ui.server import make_app

CONN = os.environ.get("KURRENTDB_CONNECTION_STRING", "kurrentdb://localhost:2113?Tls=false")


def _now() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return ts


def _new_event(msg) -> NewEvent:
    return NewEvent(
        id=uuid.uuid4(),
        type=EVENT_TYPE_NAMES[type(msg)],
        data=to_json(msg).encode("utf-8"),
        metadata=b"{}",
    )


@pytest_asyncio.fixture
async def writer():
    client = AsyncKurrentDBClient(CONN)
    try:
        yield client
    finally:
        try:
            await client.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_sse_endpoint_streams_translated_events(writer: AsyncKurrentDBClient):
    session_id = f"agui-sse-{uuid.uuid4().hex[:8]}"
    stream = agent_session_stream(session_id)

    await writer.append_to_stream(
        stream,
        events=[
            _new_event(SessionStarted(app_name="t", user_id="u", timestamp=_now())),
            _new_event(
                UserMessageReceived(
                    content="hi",
                    message_id="u1",
                    author_name="u",
                    created_at=_now(),
                    timestamp=_now(),
                )
            ),
            _new_event(
                AssistantTextGenerated(
                    content="hello",
                    message_id="a1",
                    author_name="root",
                    created_at=_now(),
                    timestamp=_now(),
                )
            ),
            _new_event(SessionEnded(reason="complete", timestamp=_now())),
        ],
        current_version=StreamState.NO_STREAM,
    )

    app = make_app(CONN)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream("GET", f"/sessions/{session_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            event_types: list[str] = []
            current_event: str | None = None
            current_data: str | None = None
            async for raw_line in resp.aiter_lines():
                line = raw_line.rstrip("\r")
                if not line:
                    if current_event and current_data:
                        event_types.append(current_event)
                        # round-trip: data is JSON of the AG-UI event dict
                        parsed = json.loads(current_data)
                        assert parsed["type"] == current_event
                    current_event = None
                    current_data = None
                    if event_types and event_types[-1] == "RUN_FINISHED":
                        break
                    continue
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    current_data = line[len("data:"):].strip()

    assert event_types == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
