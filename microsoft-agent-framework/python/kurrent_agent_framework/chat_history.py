"""Event-sourced chat history backed by KurrentDB.

Each ``Message`` is decomposed into typed canonical events on save and
reconstructed on load. Session lifecycle events (``SessionStarted`` /
``SessionEnded``) frame each stream. Event types come from the shared
:mod:`kurrent_agent_schema` package so the write/read path stays byte-identical
to the MAF .NET mirror.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from agent_framework import Content, HistoryProvider, Message, UsageDetails
from google.protobuf.json_format import MessageToDict, ParseError
from google.protobuf.message import Message as ProtoMessage
from google.protobuf.struct_pb2 import Struct
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    SessionEnded,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    agent_session_stream,
)
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import serialization
from .capture import UsageCapture

logger = logging.getLogger("kurrent_agent_framework.chat_history")


class KurrentDBHistoryProvider(HistoryProvider):
    """Persists chat history as rich typed events in KurrentDB.

    Each stored message is decomposed into one or more canonical events
    (``UserMessageReceived``, ``AssistantTextGenerated``,
    ``AssistantToolCallsGenerated``, ``ToolResultReceived``) and reconstructed
    on read.

    A ``SessionStarted`` event is emitted as the first write to a new stream.
    Call :meth:`end_session` to append a ``SessionEnded`` event when the
    conversation is complete.

    Args:
        client: Async KurrentDB client.
        source_id: Unique identifier for this provider (used by the context pipeline).
        agent_name: Recorded in the ``SessionStarted`` event. Optional.
        model_name: Recorded in the ``SessionStarted`` event. Optional.
        app_name: Recorded in the ``SessionStarted`` event. Optional. New in v2.
        usage_capture: Optional :class:`UsageCapture` middleware. When supplied,
            its recorded ``UsageDetails`` are attached as ``$usage`` metadata on
            assistant events whose ``message_id`` matches. The capture is
            cleared after each successful save.
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        source_id: str = "kurrentdb_history",
        agent_name: str | None = None,
        model_name: str | None = None,
        app_name: str | None = None,
        usage_capture: UsageCapture | None = None,
    ) -> None:
        super().__init__(source_id)
        self._client = client
        self._agent_name = agent_name
        self._model_name = model_name
        self._app_name = app_name
        self._usage_capture = usage_capture
        self._started_sessions: set[str] = set()

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        if not session_id:
            return []

        stream = agent_session_stream(session_id)
        messages: list[Message] = []

        try:
            response = await self._client.read_stream(stream)
            async for recorded in response:
                # ``kurrentdbclient`` raises NotFoundError during iteration
                # (not at ``read_stream()`` await), so the first-event iteration
                # is the earliest safe point to mark the session started. Later
                # iterations are a cheap no-op on the set.
                self._started_sessions.add(session_id)
                try:
                    event = serialization.deserialize(recorded)
                except (json.JSONDecodeError, ParseError, UnicodeDecodeError) as exc:
                    # Skip malformed/schema-mismatched events rather than aborting
                    # the whole history read. Matches the defensive behaviour of
                    # ``KurrentDBAgentMemory.recall`` and ``FactExtractionService``.
                    logger.warning(
                        "Skipping malformed canonical event at %s:%s: %r",
                        recorded.stream_name,
                        recorded.stream_position,
                        exc,
                    )
                    continue
                if event is None:
                    continue
                msg = _event_to_message(event)
                if msg is not None:
                    messages.append(msg)
        except NotFoundError:
            # First interaction — no history yet.
            pass

        return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not session_id or not messages:
            return

        stream = agent_session_stream(session_id)
        now = datetime.now(UTC)
        to_append = []

        if session_id not in self._started_sessions:
            to_append.append(
                serialization.serialize(
                    SessionStarted(
                        app_name=self._app_name,
                        agent_name=self._agent_name,
                        model=self._model_name,
                        timestamp=now,
                    )
                )
            )
            self._started_sessions.add(session_id)

        for index, message in enumerate(messages):
            metadata = self._metadata_for(message)
            for event in _message_to_events(message, message_index=index, timestamp=now):
                to_append.append(serialization.serialize(event, metadata=metadata))

        if to_append:
            await self._client.append_to_stream(
                stream_name=stream,
                current_version=StreamState.ANY,
                events=to_append,
            )

        if self._usage_capture is not None:
            self._usage_capture.clear()

    def _metadata_for(self, message: Message) -> dict[str, Any] | None:
        # Canonical ``$usage`` rides on assistant events only (``SCHEMA_v2.md
        # §3.6``). Skip user/tool/system messages even if a capture entry
        # exists under a colliding ``message_id``.
        if (
            self._usage_capture is None
            or message.role != "assistant"
            or not message.message_id
        ):
            return None
        usage = self._usage_capture.try_get(message.message_id)
        if usage is None:
            return None
        return {"$usage": _usage_to_metadata(usage)}

    async def end_session(self, session_id: str, reason: str | None = None) -> None:
        """Append a ``SessionEnded`` event to close the session stream."""
        stream = agent_session_stream(session_id)
        await self._client.append_to_stream(
            stream_name=stream,
            current_version=StreamState.ANY,
            events=[
                serialization.serialize(
                    SessionEnded(
                        reason=reason,
                        timestamp=datetime.now(UTC),
                    )
                )
            ],
        )


# --- Message <-> event conversion -------------------------------------------


def _message_to_events(
    message: Message,
    *,
    message_index: int,
    timestamp: datetime,
) -> Iterable[ProtoMessage]:
    """Decompose a ``Message`` into one or more canonical events."""
    msg_id = message.message_id
    author = message.author_name
    role = message.role

    if role == "user":
        yield UserMessageReceived(
            content=message.text,
            message_id=msg_id,
            author_name=author,
            message_index=message_index,
            timestamp=timestamp,
        )
        return

    if role == "assistant":
        tool_calls = [_build_tool_call_info(c) for c in message.contents if c.type == "function_call"]
        if tool_calls:
            yield AssistantToolCallsGenerated(
                tool_calls=tool_calls,
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        else:
            yield AssistantTextGenerated(
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        return

    if role == "tool":
        for c in message.contents:
            if c.type != "function_result":
                continue
            yield ToolResultReceived(
                call_id=c.call_id or "",
                result=_coerce_result(c.result),
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )


def _event_to_message(event: ProtoMessage) -> Message | None:
    """Reconstruct a ``Message`` from a canonical event, or ``None`` for lifecycle events."""
    if isinstance(event, UserMessageReceived):
        return Message(
            role="user",
            contents=[Content(type="text", text=event.content)],
            message_id=_opt(event, "message_id"),
            author_name=_opt(event, "author_name"),
        )
    if isinstance(event, AssistantTextGenerated):
        return Message(
            role="assistant",
            contents=[Content(type="text", text=event.content)],
            message_id=_opt(event, "message_id"),
            author_name=_opt(event, "author_name"),
        )
    if isinstance(event, AssistantToolCallsGenerated):
        contents: list[Content] = []
        if event.content:
            contents.append(Content(type="text", text=event.content))
        contents.extend(
            Content(
                type="function_call",
                call_id=tc.call_id,
                name=tc.tool_name,
                arguments=_struct_to_dict(tc.arguments) if tc.HasField("arguments") else None,
            )
            for tc in event.tool_calls
        )
        return Message(
            role="assistant",
            contents=contents,
            message_id=_opt(event, "message_id"),
            author_name=_opt(event, "author_name"),
        )
    if isinstance(event, ToolResultReceived):
        return Message(
            role="tool",
            contents=[
                Content(
                    type="function_result",
                    call_id=event.call_id,
                    result=_opt(event, "result"),
                )
            ],
            message_id=_opt(event, "message_id"),
            author_name=_opt(event, "author_name"),
        )
    return None


def _opt(message: ProtoMessage, field: str) -> str | None:
    """Read an ``optional string`` field, distinguishing unset from the empty
    default. Proto3 returns ``""`` for unset string accessors; only ``HasField``
    can tell unset from an explicit empty string."""
    return getattr(message, field) if message.HasField(field) else None


def _build_tool_call_info(content: Any) -> ToolCallInfo:
    info = ToolCallInfo(call_id=content.call_id or "", tool_name=content.name or "")
    args = _coerce_arguments(content.arguments)
    if args is not None:
        # Empty dict is preserved by design — see schema commit ff1540d.
        info.arguments.update(args)
    return info


def _coerce_arguments(arguments: Any) -> dict[str, Any] | None:
    if arguments is None:
        return None
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _struct_to_dict(struct: Struct) -> dict[str, Any]:
    """Convert a ``google.protobuf.Struct`` value to a plain Python dict."""
    return MessageToDict(struct, preserving_proto_field_name=True)


# Canonical :class:`kurrent_agent_schema.TokenUsage` slots beyond the core
# input/output/total trio. MAF providers expose these as namespaced extras —
# OpenAI emits ``openai.cached_input_tokens`` / ``openai.reasoning_tokens``,
# Anthropic emits ``anthropic.cache_read_input_tokens`` — all of which fold
# into the same canonical ``cached_input_tokens`` / ``reasoning_tokens``
# slots. Entries without a ``.``-namespace (bare ``cached_input_tokens``) are
# matched too so custom keys in Python code hit the slot.
_CANONICAL_EXTRA_SLOTS: tuple[tuple[str, str], ...] = (
    ("cached_input_tokens", "cached_input_tokens"),
    ("cache_read_input_tokens", "cached_input_tokens"),
    ("reasoning_tokens", "reasoning_tokens"),
)


def _usage_to_metadata(usage: UsageDetails) -> dict[str, Any]:
    """Map MAF ``UsageDetails`` to the canonical ``$usage`` metadata shape.

    Python's :class:`UsageDetails` is an open ``TypedDict`` — the three
    ``input_token_count`` / ``output_token_count`` / ``total_token_count``
    standard keys plus integer extras that MAF providers namespace by
    provider slug (``openai.cached_input_tokens``,
    ``anthropic.cache_read_input_tokens``, …). Canonical ``$usage`` —
    :class:`kurrent_agent_schema.TokenUsage` — has first-class
    ``cached_input_tokens`` / ``reasoning_tokens`` slots; map known extras
    into them and reserve ``additional_counts`` for truly unrecognised
    provider-specific counters. See ``schema/SCHEMA.md §3.4``.
    """
    remaining = dict(usage)
    result: dict[str, Any] = {}

    for src, dst in (
        ("input_token_count", "input_tokens"),
        ("output_token_count", "output_tokens"),
        ("total_token_count", "total_tokens"),
    ):
        if (val := remaining.pop(src, None)) is not None:
            result[dst] = val

    for key in list(remaining):
        for suffix, slot in _CANONICAL_EXTRA_SLOTS:
            if (key == suffix or key.endswith("." + suffix)) and slot not in result:
                result[slot] = remaining.pop(key)
                break

    if remaining:
        result["additional_counts"] = remaining

    return result


_APPROVAL_PROMPT_MAX_LENGTH: int = 200


def _build_approval_prompt(*, name: str, arguments: dict[str, Any] | None) -> str:
    """Synthesize a display-only approval prompt. Display-only — no code parses it.

    Mirrors :py:func:`Kurrent.AgentFramework.Serialization.ChatMessageConverter.BuildApprovalPrompt`
    on the .NET side. Truncates at 200 chars; falls back to ``"Approve calling {name}?"``
    when the args render past the cap, and hard-caps the fallback when the name alone
    exceeds the cap.
    """
    head = f"Approve calling {name}"

    if not arguments:
        return _hard_cap(f"{head}?")

    parts = [f"{key}={json.dumps(value)}" for key, value in arguments.items()]
    full = f"{head}({', '.join(parts)})?"
    if len(full) <= _APPROVAL_PROMPT_MAX_LENGTH:
        return full

    without_args = f"{head}?"
    if len(without_args) >= _APPROVAL_PROMPT_MAX_LENGTH:
        return _hard_cap(without_args)

    available = _APPROVAL_PROMPT_MAX_LENGTH - len(f"{head}(…)?")
    if available <= 0:
        return _hard_cap(without_args)
    return f"{head}({', '.join(parts)[:available]}…)?"


def _hard_cap(value: str) -> str:
    """Cap a string at the max prompt length."""
    return value if len(value) <= _APPROVAL_PROMPT_MAX_LENGTH else value[:_APPROVAL_PROMPT_MAX_LENGTH]


def _build_afw_interrupt_extension(
    *,
    call_id: str,
    name: str,
    arguments: dict[str, Any] | None,
    approval_pair_id: str | None,
) -> dict[str, Any]:
    """Build the ``extensions["afw"]`` block for InterruptIssued/Resolved events.

    Mirrors :py:func:`Kurrent.AgentFramework.Serialization.ChatMessageConverter.BuildAfwInterruptExtension`
    on the .NET side. ``approval_pair_id`` is omitted when it equals ``call_id``
    (the common case in MAF where the wrapper threads the call id through unchanged).

    The returned dict gets converted to a ``google.protobuf.Struct`` later via
    ``ParseDict`` when stamped on the event's ``extensions`` map.
    """
    proposed: dict[str, Any] = {
        "id": call_id,
        "name": name,
        "arguments": arguments if arguments is not None else {},
    }
    interrupt: dict[str, Any] = {"proposed_call": proposed}
    if approval_pair_id and approval_pair_id != call_id:
        interrupt["approval_pair_id"] = approval_pair_id
    return {"interrupt": interrupt}


def _coerce_result(result: Any) -> str | None:
    if result is None:
        return None
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return str(result)
