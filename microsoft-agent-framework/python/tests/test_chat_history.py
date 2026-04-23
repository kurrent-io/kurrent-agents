"""Unit tests for ``KurrentDBHistoryProvider``.

Uses an in-memory fake client (same pattern as ``test_memory.py``) so these
tests don't need a running KurrentDB.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from agent_framework import Message, UsageDetails
from kurrentdbclient import NewEvent, RecordedEvent
from kurrentdbclient.exceptions import NotFoundError

from kurrent_agent_framework import KurrentDBHistoryProvider, UsageCapture


class _FakeResponse:
    def __init__(self, events: list[RecordedEvent]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[RecordedEvent]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[RecordedEvent]:
        for event in self._events:
            yield event


class FakeClient:
    def __init__(self) -> None:
        self.streams: dict[str, list[RecordedEvent]] = {}

    async def append_to_stream(
        self,
        *,
        stream_name: str,
        current_version: Any,
        events: list[NewEvent],
    ) -> None:
        bucket = self.streams.setdefault(stream_name, [])
        start = len(bucket)
        for idx, new in enumerate(events):
            bucket.append(
                RecordedEvent(
                    type=new.type,
                    data=new.data,
                    metadata=new.metadata,
                    content_type="application/json",
                    id=new.id,
                    stream_name=stream_name,
                    stream_position=start + idx,
                    commit_position=start + idx,
                    prepare_position=start + idx,
                    recorded_at=datetime.now(UTC),
                )
            )

    async def read_stream(self, stream_name: str, **_: Any) -> _FakeResponse:
        if stream_name not in self.streams:
            raise NotFoundError(f"stream {stream_name!r} not found")
        return _FakeResponse(list(self.streams[stream_name]))


async def test_get_messages_skips_malformed_events_and_continues() -> None:
    """A canonical event with broken JSON, wrong UTF-8, or failing Pydantic
    validation must not abort the history read — the provider should log and
    skip, matching the defensive behaviour of ``KurrentDBAgentMemory.recall``
    and ``FactExtractionService``."""
    client = FakeClient()
    history = KurrentDBHistoryProvider(client)  # type: ignore[arg-type]

    stream = "AgentSession-s1"
    # Mix three events in stream order: broken JSON, a well-formed user message,
    # and a canonical type that fails validation (missing required field).
    await client.append_to_stream(
        stream_name=stream,
        current_version=None,
        events=[
            NewEvent(type="UserMessageReceived", data=b"not-json-at-all"),
            NewEvent(
                type="UserMessageReceived",
                data=b'{"content":"hello","message_index":0,"timestamp":"2026-04-22T10:00:00Z"}',
            ),
            NewEvent(type="UserMessageReceived", data=b'{"timestamp":"2026-04-22T10:00:01Z"}'),  # missing message_index
        ],
    )

    messages = await history.get_messages("s1")
    assert len(messages) == 1
    assert messages[0].text == "hello"


async def test_save_messages_attaches_usage_metadata_when_capture_matches() -> None:
    """When a ``UsageCapture`` holds usage for an assistant message's id, the
    provider stamps ``$usage`` metadata on the emitted assistant event."""
    client = FakeClient()
    capture = UsageCapture()
    history = KurrentDBHistoryProvider(client, usage_capture=capture)  # type: ignore[arg-type]

    # Pretend the middleware already recorded usage for this assistant message.
    capture._usages["msg-1"] = UsageDetails(
        input_token_count=10,
        output_token_count=20,
        total_token_count=30,
        # Provider-specific extras — TypedDict allows integer extras.
        cached_input_token_count=4,
    )

    await history.save_messages(
        "s1",
        [
            Message("user", ["hi"], message_id="user-msg"),
            Message("assistant", ["hello"], message_id="msg-1"),
        ],
    )

    events = client.streams["AgentSession-s1"]
    # [0] SessionStarted, [1] UserMessageReceived, [2] AssistantTextGenerated
    assistant_event = events[2]
    assert assistant_event.type == "AssistantTextGenerated"

    metadata = json.loads(assistant_event.metadata)
    usage = metadata["$usage"]
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 20
    assert usage["total_tokens"] == 30
    # Extra keys outside the canonical trio land in additional_counts.
    assert usage["additional_counts"] == {"cached_input_token_count": 4}

    # User message has no captured usage — only $schema_version metadata.
    user_metadata = json.loads(events[1].metadata)
    assert "$usage" not in user_metadata

    # Capture is cleared after save so the next turn starts fresh.
    assert capture.try_get("msg-1") is None


async def test_save_messages_without_capture_omits_usage_metadata() -> None:
    """Without a ``UsageCapture``, the emitted events carry only the schema
    version marker — no ``$usage`` key."""
    client = FakeClient()
    history = KurrentDBHistoryProvider(client)  # type: ignore[arg-type]

    await history.save_messages(
        "s1",
        [Message("assistant", ["hello"], message_id="msg-1")],
    )

    events = client.streams["AgentSession-s1"]
    assistant_event = events[1]
    assert assistant_event.type == "AssistantTextGenerated"
    metadata = json.loads(assistant_event.metadata)
    assert "$usage" not in metadata
