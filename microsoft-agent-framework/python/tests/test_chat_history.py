"""Unit tests for ``KurrentDBHistoryProvider``.

Uses an in-memory fake client (same pattern as ``test_memory.py``) so these
tests don't need a running KurrentDB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from kurrentdbclient import NewEvent, RecordedEvent
from kurrentdbclient.exceptions import NotFoundError

from kurrent_agent_framework import KurrentDBHistoryProvider


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
