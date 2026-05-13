"""Shared dummy-mode canonical writer for the showcase native lanes.

When the lane runner can't (or doesn't want to) actually invoke its
framework integration — e.g. no LLM API key, or just for the smoke
demo — it can call ``write_dummy_session`` to append a canned
canonical conversation to KurrentDB. The TS server's DEV-1559 live
tail picks the events up and re-emits them as AG-UI events.

Each lane can stamp its own ``app_name``, ``agent_name``, and ``model``
so the demo's KurrentDB admin browser shows the lanes side-by-side.

Real-mode runners replace the call to ``write_dummy_session`` with
their integration's actual KurrentDB session-write path.
"""

from __future__ import annotations

import asyncio
import json
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


async def write_dummy_session(
    client: AsyncKurrentDBClient,
    *,
    session_id: str,
    user_message: str,
    app_name: str,
    agent_name: str,
    model: str,
) -> None:
    """Append a canned six-event canonical conversation to KurrentDB.

    Sequence: SessionStarted (only on new session) → UserMessageReceived
    → AssistantToolCallsGenerated (carrier text + weather lookup) →
    ToolResultReceived → AssistantTextGenerated → SessionEnded.

    A small async sleep between appends gives the live-tail subscriber
    visible streaming latency. In a real LLM run this latency comes
    naturally from token generation.
    """
    stream = agent_session_stream(session_id)
    run_id = f"run-{uuid.uuid4().hex[:8]}"

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
                    app_name=app_name,
                    agent_name=agent_name,
                    model=model,
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
                content=f"({agent_name}) Looking that up.",
                message_id=asst1_id,
                author_name=agent_name,
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
        f'(dummy mode reply via {agent_name}) You asked "{user_message}". '
        f"It's sunny and 22°C wherever you are."
    )
    events.append(
        _new_event(
            AssistantTextGenerated(
                content=canned_reply,
                message_id=asst2_id,
                author_name=agent_name,
                created_at=_now_ts(),
                timestamp=_now_ts(),
            ),
            run_id=run_id,
        )
    )

    events.append(
        _new_event(SessionEnded(reason="complete", timestamp=_now_ts()), run_id=run_id)
    )

    for e in events:
        await client.append_to_stream(stream, events=[e], current_version=StreamState.ANY)
        await asyncio.sleep(0.25)
