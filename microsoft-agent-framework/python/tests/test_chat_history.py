"""Unit tests for ``KurrentDBHistoryProvider``.

Uses an in-memory fake client (same pattern as ``test_memory.py``) so these
tests don't need a running KurrentDB. The round-trip tests at the bottom
require a live KurrentDB instance via the ``kurrentdb_client`` fixture.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from agent_framework import ChatContext, ChatResponse, Content, Message, UsageDetails
from kurrentdbclient import NewEvent, RecordedEvent
from kurrentdbclient.exceptions import NotFoundError

from kurrent_agent_framework import KurrentDBHistoryProvider, UsageCapture


async def _prime_capture(capture: UsageCapture, response: ChatResponse) -> None:
    """Feed ``response`` through the capture middleware the same way
    ``ChatMiddlewarePipeline`` would on a non-streaming chat call. Avoids
    poking ``capture._usages`` directly in tests."""
    context = ChatContext(
        client=cast(Any, object()),
        messages=[],
        options=None,
        stream=False,
    )

    async def _call_next() -> None:
        context.result = response

    await capture.process(context, _call_next)


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
    # and a JSON payload whose field types don't match the canonical schema.
    # Protobuf's JSON parser ignores unknown fields but rejects type mismatches.
    await client.append_to_stream(
        stream_name=stream,
        current_version=None,
        events=[
            NewEvent(type="UserMessageReceived", data=b"not-json-at-all"),
            NewEvent(
                type="UserMessageReceived",
                data=b'{"content":"hello","message_index":0,"timestamp":"2026-04-22T10:00:00Z"}',
            ),
            NewEvent(
                type="UserMessageReceived",
                data=b'{"message_index":"not-a-number","timestamp":"2026-04-22T10:00:01Z"}',
            ),
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

    # Shape of a ChatResponse the OpenAI provider would emit: the three
    # standard UsageDetails keys plus ``openai.cached_input_tokens`` /
    # ``openai.reasoning_tokens`` namespaced extras (see
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
    await _prime_capture(
        capture,
        ChatResponse(
            messages=[Message("assistant", ["hello"], message_id="msg-1")],
            usage_details=usage,
        ),
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
    client = FakeClient()
    capture = UsageCapture()
    history = KurrentDBHistoryProvider(client, usage_capture=capture)  # type: ignore[arg-type]

    # Capture an assistant response at id "shared-id" — then the save below
    # re-uses that same id on a user and a tool message.
    await _prime_capture(
        capture,
        ChatResponse(
            messages=[Message("assistant", ["assist"], message_id="shared-id")],
            usage_details=UsageDetails(input_token_count=99),
        ),
    )

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


def test_build_approval_prompt_simple_args():
    from kurrent_agent_framework.chat_history import _build_approval_prompt

    prompt = _build_approval_prompt(name="send_email", arguments={"to": "alice@x.com", "subject": "hi"})

    assert prompt == 'Approve calling send_email(to="alice@x.com", subject="hi")?'


def test_build_approval_prompt_no_args():
    from kurrent_agent_framework.chat_history import _build_approval_prompt

    assert _build_approval_prompt(name="ping", arguments=None) == "Approve calling ping?"


def test_build_approval_prompt_truncates_long_args():
    from kurrent_agent_framework.chat_history import _APPROVAL_PROMPT_MAX_LENGTH, _build_approval_prompt

    prompt = _build_approval_prompt(name="huge", arguments={"payload": "x" * 500})

    assert len(prompt) == _APPROVAL_PROMPT_MAX_LENGTH
    assert prompt.endswith("…)?")
    assert prompt.startswith('Approve calling huge(payload="')


def test_build_approval_prompt_name_longer_than_cap_hard_caps_fallback():
    from kurrent_agent_framework.chat_history import _APPROVAL_PROMPT_MAX_LENGTH, _build_approval_prompt

    prompt = _build_approval_prompt(name="n" * 250, arguments=None)

    assert len(prompt) == _APPROVAL_PROMPT_MAX_LENGTH
    assert prompt.startswith("Approve calling n")


def test_build_afw_interrupt_extension_call_id_equals_pair_id_omits_pair_id():
    from kurrent_agent_framework.chat_history import _build_afw_interrupt_extension

    ext = _build_afw_interrupt_extension(
        call_id="call-1", name="ping", arguments={"x": 1}, approval_pair_id="call-1",
    )

    interrupt = ext["interrupt"]
    assert "approval_pair_id" not in interrupt
    proposed = interrupt["proposed_call"]
    assert proposed == {"id": "call-1", "name": "ping", "arguments": {"x": 1}}


def test_build_afw_interrupt_extension_differing_pair_id_includes_pair_id():
    from kurrent_agent_framework.chat_history import _build_afw_interrupt_extension

    ext = _build_afw_interrupt_extension(
        call_id="call-1", name="ping", arguments=None, approval_pair_id="approval-pair-9",
    )

    assert ext["interrupt"]["approval_pair_id"] == "approval-pair-9"
    proposed = ext["interrupt"]["proposed_call"]
    assert proposed["id"] == "call-1"
    assert proposed["name"] == "ping"
    assert proposed["arguments"] == {}


# --- Approval decomposition helpers ---


def _make_function_call(call_id: str, name: str, arguments: dict[str, Any] | None = None):
    from agent_framework import Content

    return Content(type="function_call", call_id=call_id, name=name, arguments=arguments)


def _make_approval_request(call_id: str, name: str, arguments: dict[str, Any] | None = None,
                           pair_id: str | None = None):
    from agent_framework import Content

    return Content.from_function_approval_request(
        id=pair_id or call_id,
        function_call=_make_function_call(call_id, name, arguments),
    )


def _make_approval_response(call_id: str, approved: bool, name: str = "ping",
                            arguments: dict[str, Any] | None = None,
                            pair_id: str | None = None):
    from agent_framework import Content

    return Content.from_function_approval_response(
        approved=approved,
        id=pair_id or call_id,
        function_call=_make_function_call(call_id, name, arguments),
    )


def test_message_to_events_assistant_text_and_approval_emits_text_and_interrupt():
    from agent_framework import Content, Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import AssistantTextGenerated, InterruptIssued

    msg = Message(
        role="assistant",
        contents=[
            Content(type="text", text="Drafting an email."),
            _make_approval_request("call-1", "send_email", {"to": "alice"}),
        ],
        message_id="asst-msg-1",
    )

    events = list(_message_to_events(msg, message_index=0, timestamp=datetime.now(UTC)))

    assert len(events) == 2
    assert isinstance(events[0], AssistantTextGenerated)
    assert events[0].content == "Drafting an email."
    assert events[0].message_id == "asst-msg-1"

    ii = events[1]
    assert isinstance(ii, InterruptIssued)
    assert ii.request_id == "call-1"
    assert ii.kind == "approval"
    assert ii.tool_name == "send_email"
    assert ii.message_id == "asst-msg-1"
    assert ii.prompt.startswith("Approve calling send_email(")

    from google.protobuf.json_format import MessageToDict
    afw = MessageToDict(ii.extensions["afw"], preserving_proto_field_name=True)
    assert afw["interrupt"]["proposed_call"]["name"] == "send_email"


def test_message_to_events_assistant_approval_only_emits_marker_and_interrupt():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import AssistantTextGenerated, InterruptIssued

    msg = Message(
        role="assistant",
        contents=[_make_approval_request("call-1", "ping")],
        message_id="asst-msg-2",
    )

    events = list(_message_to_events(msg, message_index=5, timestamp=datetime.now(UTC)))

    assert len(events) == 2
    marker = events[0]
    assert isinstance(marker, AssistantTextGenerated)
    assert not marker.HasField("content")
    assert marker.message_index == 5
    assert marker.message_id == "asst-msg-2"
    assert isinstance(events[1], InterruptIssued)


def test_message_to_events_user_approval_response_emits_marker_and_resolved():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import InterruptResolved, UserMessageReceived

    msg = Message(
        role="user",
        contents=[_make_approval_response("call-1", approved=True, name="send_email",
                                          arguments={"to": "alice"})],
        message_id="user-msg-1",
    )

    events = list(_message_to_events(msg, message_index=1, timestamp=datetime.now(UTC)))

    assert len(events) == 2
    marker = events[0]
    assert isinstance(marker, UserMessageReceived)
    assert not marker.HasField("content")
    assert marker.message_index == 1

    ir = events[1]
    assert isinstance(ir, InterruptResolved)
    assert ir.request_id == "call-1"
    assert ir.outcome == "allow"
    assert ir.message_id == "user-msg-1"
    from google.protobuf.json_format import MessageToDict
    afw = MessageToDict(ir.extensions["afw"], preserving_proto_field_name=True)
    assert afw["interrupt"]["proposed_call"]["name"] == "send_email"


def test_message_to_events_user_approval_response_denied_emits_deny():
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import _message_to_events
    from kurrent_agent_schema import InterruptResolved

    msg = Message(
        role="user",
        contents=[_make_approval_response("call-1", approved=False)],
    )

    events = list(_message_to_events(msg, message_index=0, timestamp=datetime.now(UTC)))

    # marker + resolved
    assert len(events) == 2
    ir = events[1]
    assert isinstance(ir, InterruptResolved)
    assert ir.outcome == "deny"


def test_merge_events_assistant_text_and_interrupt_rebuilds_contents():
    from agent_framework import Content, Message
    from kurrent_agent_framework.chat_history import _merge_events_into_message, _message_to_events

    original = Message(
        role="assistant",
        contents=[
            Content(type="text", text="Drafting…"),
            _make_approval_request("call-1", "send_email", {"to": "alice"}),
        ],
        message_id="asst-msg-1",
    )
    events = list(_message_to_events(original, message_index=0, timestamp=datetime.now(UTC)))
    issued_by_request_id = {
        e.request_id: e for e in events
        if e.__class__.__name__ == "InterruptIssued"
    }

    rebuilt = _merge_events_into_message(events, issued_by_request_id)

    assert rebuilt is not None
    assert rebuilt.role == "assistant"
    assert rebuilt.message_id == "asst-msg-1"
    text_blocks = [c for c in rebuilt.contents if c.type == "text"]
    assert text_blocks[0].text == "Drafting…"
    approvals = [c for c in rebuilt.contents if c.type == "function_approval_request"]
    assert len(approvals) == 1
    assert approvals[0].function_call.name == "send_email"
    assert approvals[0].function_call.call_id == "call-1"


@pytest.mark.asyncio
async def test_approval_request_round_trips_through_kurrentdb(kurrentdb_client: Any):
    """End-to-end: write an assistant [Text, ApprovalRequest] message and read
    it back through the provider; the rebuilt Message must contain both blocks."""
    from agent_framework import Content, Message
    from kurrent_agent_framework.chat_history import KurrentDBHistoryProvider

    session_id = uuid.uuid4().hex
    provider = KurrentDBHistoryProvider(kurrentdb_client)
    carrier = Message(
        role="assistant",
        contents=[
            Content(type="text", text="Drafting…"),
            _make_approval_request("call-1", "send_email", {"to": "alice"}),
        ],
        message_id="asst-msg-1",
    )
    await provider.save_messages(session_id, [carrier])

    # Fresh provider over same stream
    reader = KurrentDBHistoryProvider(kurrentdb_client)
    rebuilt_messages = await reader.get_messages(session_id)

    assert len(rebuilt_messages) == 1
    rebuilt = rebuilt_messages[0]
    assert rebuilt.role == "assistant"
    assert rebuilt.message_id == "asst-msg-1"
    text_blocks = [c for c in rebuilt.contents if c.type == "text"]
    assert text_blocks[0].text == "Drafting…"
    approvals = [c for c in rebuilt.contents if c.type == "function_approval_request"]
    assert len(approvals) == 1
    assert approvals[0].function_call.name == "send_email"


@pytest.mark.asyncio
async def test_approval_response_round_trips_through_kurrentdb(kurrentdb_client: Any):
    """End-to-end: assistant approval request + user approval response.
    Both events serialize/deserialize through KurrentDB and reconstruct
    via the provider's grouping pass + cross-event lookup."""
    from agent_framework import Message
    from kurrent_agent_framework.chat_history import KurrentDBHistoryProvider

    session_id = uuid.uuid4().hex
    provider = KurrentDBHistoryProvider(kurrentdb_client)
    assistant = Message(
        role="assistant",
        contents=[_make_approval_request("call-1", "send_email", {"to": "alice"})],
        message_id="asst-msg-1",
    )
    user_response = Message(
        role="user",
        contents=[_make_approval_response("call-1", approved=True, name="send_email",
                                          arguments={"to": "alice"})],
        message_id="user-msg-2",
    )
    await provider.save_messages(session_id, [assistant, user_response])

    reader = KurrentDBHistoryProvider(kurrentdb_client)
    rebuilt_messages = await reader.get_messages(session_id)

    assert len(rebuilt_messages) == 2
    rebuilt_user = rebuilt_messages[-1]
    assert rebuilt_user.role == "user"
    assert rebuilt_user.message_id == "user-msg-2"

    responses = [c for c in rebuilt_user.contents if c.type == "function_approval_response"]
    assert len(responses) == 1
    assert responses[0].approved is True
    assert responses[0].function_call.name == "send_email"
    assert responses[0].function_call.call_id == "call-1"
