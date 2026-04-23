"""Tests for :class:`UsageCapture`.

Mirrors the .NET ``UsageCaptureTests`` suite. Exercises the middleware in
isolation — no KurrentDB connection needed. The streaming cases simulate the
pipeline's post-processing step by attaching ``stream_result_hooks`` onto the
returned :class:`ResponseStream` and iterating it to completion, which is what
``ChatMiddlewarePipeline`` does in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, cast

from agent_framework import (
    ChatContext,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
    UsageDetails,
)

from kurrent_agent_framework import UsageCapture


def _make_context(*, stream: bool = False) -> ChatContext:
    """Build a minimal :class:`ChatContext`. The middleware never touches
    ``client`` / ``messages`` / ``options``, so stubs are fine."""
    return ChatContext(
        client=cast(Any, object()),
        messages=[],
        options=None,
        stream=stream,
    )


async def _run_non_streaming(capture: UsageCapture, response: ChatResponse) -> None:
    """Drive the middleware the way ``ChatMiddlewarePipeline`` would for a
    non-streaming call: run ``process()`` with a ``call_next`` that assigns
    ``context.result``."""
    context = _make_context(stream=False)

    async def call_next() -> None:
        context.result = response

    await capture.process(context, call_next)


async def _iter_updates(updates: Sequence[ChatResponseUpdate]) -> AsyncIterator[ChatResponseUpdate]:
    for u in updates:
        yield u


async def _run_streaming(
    capture: UsageCapture,
    updates: Sequence[ChatResponseUpdate],
) -> list[ChatResponseUpdate]:
    """Drive the middleware for a streaming call. Returns the updates as
    observed by the caller so tests can assert forwarding behaviour."""
    context = _make_context(stream=True)

    stream: ResponseStream[ChatResponseUpdate, ChatResponse] = ResponseStream(
        _iter_updates(updates),
        finalizer=ChatResponse.from_updates,
    )

    async def call_next() -> None:
        context.result = stream

    await capture.process(context, call_next)

    # Mirror what ChatMiddlewarePipeline does once the middleware chain
    # returns: wire the hooks collected on the context onto the stream.
    assert isinstance(context.result, ResponseStream)
    for hook in context.stream_result_hooks:
        context.result.with_result_hook(hook)

    seen: list[ChatResponseUpdate] = []
    async for update in context.result:
        seen.append(update)
    # Force finalization so result hooks fire.
    await context.result.get_final_response()
    return seen


async def test_non_streaming_attaches_usage_to_every_message_id() -> None:
    usage = UsageDetails(input_token_count=10, output_token_count=20, total_token_count=30)
    response = ChatResponse(
        messages=[
            Message("assistant", ["first"], message_id="msg-1"),
            Message("assistant", ["second"], message_id="msg-2"),
        ],
        usage_details=usage,
    )

    capture = UsageCapture()
    await _run_non_streaming(capture, response)

    a = capture.try_get("msg-1")
    assert a is not None
    assert a["input_token_count"] == 10
    b = capture.try_get("msg-2")
    assert b is not None
    assert b["output_token_count"] == 20


async def test_non_streaming_without_usage_records_nothing() -> None:
    response = ChatResponse(messages=[Message("assistant", ["hi"], message_id="msg-x")])

    capture = UsageCapture()
    await _run_non_streaming(capture, response)

    assert capture.try_get("msg-x") is None


async def test_non_streaming_skips_messages_without_message_id() -> None:
    usage = UsageDetails(input_token_count=5)
    response = ChatResponse(
        messages=[
            Message("assistant", ["unnamed"]),  # no message_id
            Message("assistant", ["named"], message_id="msg-1"),
        ],
        usage_details=usage,
    )

    capture = UsageCapture()
    await _run_non_streaming(capture, response)

    u = capture.try_get("msg-1")
    assert u is not None
    assert u["input_token_count"] == 5


async def test_streaming_attaches_usage_to_message_id() -> None:
    usage = UsageDetails(input_token_count=7, output_token_count=3)

    updates = [
        ChatResponseUpdate(contents=[Content(type="text", text="hel")], role="assistant", message_id="msg-1"),
        ChatResponseUpdate(contents=[Content(type="text", text="lo")], role="assistant", message_id="msg-1"),
        # Usage typically arrives in a final update.
        ChatResponseUpdate(
            contents=[Content.from_usage(usage)],
            role="assistant",
            message_id="msg-1",
        ),
    ]

    capture = UsageCapture()
    await _run_streaming(capture, updates)

    u = capture.try_get("msg-1")
    assert u is not None
    assert u["input_token_count"] == 7
    assert u["output_token_count"] == 3


async def test_streaming_without_usage_records_nothing() -> None:
    updates = [
        ChatResponseUpdate(contents=[Content(type="text", text="hel")], role="assistant", message_id="msg-1"),
        ChatResponseUpdate(contents=[Content(type="text", text="lo")], role="assistant", message_id="msg-1"),
    ]

    capture = UsageCapture()
    await _run_streaming(capture, updates)

    assert capture.try_get("msg-1") is None


async def test_clear_empties_the_captured_usage_map() -> None:
    usage = UsageDetails(input_token_count=1)
    response = ChatResponse(
        messages=[Message("assistant", ["x"], message_id="msg-1")],
        usage_details=usage,
    )

    capture = UsageCapture()
    await _run_non_streaming(capture, response)

    assert capture.try_get("msg-1") is not None

    capture.clear()

    assert capture.try_get("msg-1") is None


async def test_streaming_forwards_all_updates_to_caller() -> None:
    updates = [
        ChatResponseUpdate(contents=[Content(type="text", text="one")], role="assistant", message_id="msg-1"),
        ChatResponseUpdate(contents=[Content(type="text", text="two")], role="assistant", message_id="msg-1"),
        ChatResponseUpdate(contents=[Content(type="text", text="three")], role="assistant", message_id="msg-1"),
    ]

    capture = UsageCapture()
    seen = await _run_streaming(capture, updates)

    texts = [u.text for u in seen]
    assert texts == ["one", "two", "three"]


async def test_pure_chat_middleware_callable_contract() -> None:
    """Sanity check: ``UsageCapture`` honours the ``ChatMiddleware`` ABC so
    it can be slotted into ``middleware=[capture]``."""
    from agent_framework import ChatMiddleware

    capture = UsageCapture()
    assert isinstance(capture, ChatMiddleware)
    assert callable(getattr(capture, "process", None))
    # Silence unused-import warning in environments without the marker.
    _: Callable[[], Awaitable[None]]
