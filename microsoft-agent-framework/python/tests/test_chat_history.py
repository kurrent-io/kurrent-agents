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
    provider stamps ``$usage`` metadata on the emitted assistant event — with
    upstream provider-namespaced extras folded into canonical slots."""
    client = FakeClient()
    capture = UsageCapture()
    history = KurrentDBHistoryProvider(client, usage_capture=capture)  # type: ignore[arg-type]

    # Pretend the middleware recorded usage produced by the OpenAI provider:
    # the three standard keys plus `openai.cached_input_tokens` /
    # `openai.reasoning_tokens` namespaced extras (see
    # ``agent_framework_openai._chat_client._parse_usage_from_openai``).
    # Anthropic's ``anthropic.cache_creation_input_tokens`` rides along as a
    # true provider-specific counter with no canonical home.
    usage = UsageDetails(
        input_token_count=10,
        output_token_count=20,
        total_token_count=30,
    )
    usage["openai.cached_input_tokens"] = 4  # type: ignore[typeddict-unknown-key]
    usage["openai.reasoning_tokens"] = 7  # type: ignore[typeddict-unknown-key]
    usage["anthropic.cache_creation_input_tokens"] = 40  # type: ignore[typeddict-unknown-key]
    capture._usages["msg-1"] = usage

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
    usage_meta = metadata["$usage"]
    assert usage_meta["input_tokens"] == 10
    assert usage_meta["output_tokens"] == 20
    assert usage_meta["total_tokens"] == 30
    # Namespaced extras with a canonical home land at top-level, not under
    # additional_counts.
    assert usage_meta["cached_input_tokens"] == 4
    assert usage_meta["reasoning_tokens"] == 7
    # Truly provider-specific counters (no canonical slot) fall through.
    assert usage_meta["additional_counts"] == {"anthropic.cache_creation_input_tokens": 40}

    # User message has no captured usage — only $schema_version metadata.
    user_metadata = json.loads(events[1].metadata)
    assert "$usage" not in user_metadata

    # Capture is cleared after save so the next turn starts fresh.
    assert capture.try_get("msg-1") is None


async def test_save_messages_does_not_stamp_usage_on_non_assistant_events() -> None:
    """``$usage`` belongs on assistant events only (``SCHEMA_v2 §3.6``). A
    capture entry whose id happens to match a user/tool message must not
    leak metadata onto ``UserMessageReceived`` or ``ToolResultReceived``."""
    from agent_framework import Content

    client = FakeClient()
    capture = UsageCapture()
    history = KurrentDBHistoryProvider(client, usage_capture=capture)  # type: ignore[arg-type]

    # Both messages share the id the capture holds.
    capture._usages["shared-id"] = UsageDetails(input_token_count=99)

    await history.save_messages(
        "s1",
        [
            Message("user", ["hi"], message_id="shared-id"),
            Message(
                "tool",
                [Content(type="function_result", call_id="call-1", result="ok")],
                message_id="shared-id",
            ),
        ],
    )

    events = client.streams["AgentSession-s1"]
    # [0] SessionStarted, [1] UserMessageReceived, [2] ToolResultReceived
    for event in events[1:]:
        metadata = json.loads(event.metadata)
        assert "$usage" not in metadata, f"$usage leaked onto {event.type}"


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
