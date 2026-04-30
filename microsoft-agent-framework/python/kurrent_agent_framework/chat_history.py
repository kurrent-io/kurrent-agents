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
    InterruptIssued,
    InterruptResolved,
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
        groups: list[list[ProtoMessage]] = []
        by_message_id: dict[str, int] = {}
        issued_by_request_id: dict[str, InterruptIssued] = {}

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

                if isinstance(event, InterruptIssued):
                    issued_by_request_id[event.request_id] = event

                key = _grouping_key(event)
                if key and key in by_message_id:
                    groups[by_message_id[key]].append(event)
                else:
                    groups.append([event])
                    if key:
                        by_message_id[key] = len(groups) - 1
        except NotFoundError:
            # First interaction — no history yet.
            pass

        for group in groups:
            msg = _merge_events_into_message(group, issued_by_request_id)
            if msg is not None:
                messages.append(msg)

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
    """Decompose a ``Message`` into one or more canonical events.

    Approval content blocks (``function_approval_request`` /
    ``function_approval_response``) are decomposed into ``InterruptIssued`` /
    ``InterruptResolved`` events that share ``message_id`` with the carrier
    message. Approval-only turns still emit a content-less
    ``AssistantTextGenerated`` / ``UserMessageReceived`` marker so
    ``message_index`` continuity is preserved across stream rehydration; the
    read path filters empty content and the rebuilt ``Message`` carries only
    the approval content blocks. See SCHEMA_v2 §3.3 and
    ``docs/superpowers/specs/2026-04-29-maf-tool-approval-interrupts-design.md``.
    """
    msg_id = message.message_id
    author = message.author_name
    role = message.role

    if role == "user":
        responses = [c for c in message.contents if c.type == "function_approval_response"]

        if message.text or responses:
            evt = UserMessageReceived(
                content=message.text or None,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
            yield evt

        for response in responses:
            yield _build_interrupt_resolved(response, message, timestamp)
        return

    if role == "assistant":
        tool_calls = [_build_tool_call_info(c) for c in message.contents if c.type == "function_call"]
        approvals = [c for c in message.contents if c.type == "function_approval_request"]

        if tool_calls:
            yield AssistantToolCallsGenerated(
                tool_calls=tool_calls,
                content=message.text or None,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        elif message.text or approvals:
            yield AssistantTextGenerated(
                content=message.text or None,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )

        for approval in approvals:
            yield _build_interrupt_issued(approval, message, timestamp)
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


def _grouping_key(event: ProtoMessage) -> str | None:
    """Return the ``message_id`` if the event carries one, else ``None``.

    ``SessionStarted`` / ``SessionEnded`` and other lifecycle events lack the
    field entirely; ``HasField`` raises ``ValueError`` for unknown field names,
    so we guard with a try/except.
    """
    if not hasattr(event, "HasField"):
        return None
    try:
        if event.HasField("message_id"):
            return event.message_id
    except ValueError:
        pass
    return None


def _determine_role(events: Sequence[ProtoMessage]) -> str | None:
    """Infer message role from the first role-bearing event in the group."""
    for ev in events:
        if isinstance(ev, (UserMessageReceived, InterruptResolved)):
            return "user"
        if isinstance(ev, (AssistantTextGenerated, AssistantToolCallsGenerated, InterruptIssued)):
            return "assistant"
        if isinstance(ev, ToolResultReceived):
            return "tool"
    return None


def _merge_events_into_message(
    events: Sequence[ProtoMessage],
    issued_by_request_id: dict[str, InterruptIssued],
) -> Message | None:
    """Build a single ``Message`` from a group of canonical events sharing
    a ``message_id``. Returns None when the group has no chat-shaped events.

    Mirrors :py:func:`Kurrent.AgentFramework.Serialization.ChatMessageConverter.MergeIntoChatMessage`
    on the .NET side. Empty-content markers (e.g. an ``AssistantTextGenerated``
    with ``HasField('content') == False``, used to anchor ``message_index`` for
    approval-only turns) contribute their ``message_id`` but no ``Content``.
    """
    if not events:
        return None

    role = _determine_role(events)
    if role is None:
        return None

    contents: list[Content] = []
    msg_id: str | None = None
    author: str | None = None

    for ev in events:
        if isinstance(ev, UserMessageReceived):
            if ev.HasField("content") and ev.content:
                contents.append(Content(type="text", text=ev.content))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, AssistantTextGenerated):
            if ev.HasField("content") and ev.content:
                contents.append(Content(type="text", text=ev.content))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, AssistantToolCallsGenerated):
            if ev.HasField("content") and ev.content:
                contents.append(Content(type="text", text=ev.content))
            for tc in ev.tool_calls:
                contents.append(Content(
                    type="function_call",
                    call_id=tc.call_id,
                    name=tc.tool_name,
                    arguments=_struct_to_dict(tc.arguments) if tc.HasField("arguments") else None,
                ))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, ToolResultReceived):
            contents.append(Content(
                type="function_result",
                call_id=ev.call_id,
                result=_opt(ev, "result"),
            ))
            msg_id = msg_id or _opt(ev, "message_id")
            author = author or _opt(ev, "author_name")
        elif isinstance(ev, InterruptIssued):
            contents.append(_build_approval_request_content(ev))
            msg_id = msg_id or _opt(ev, "message_id")
        elif isinstance(ev, InterruptResolved):
            built = _build_approval_response_content(ev, issued_by_request_id)
            if built is not None:
                contents.append(built)
            msg_id = msg_id or _opt(ev, "message_id")

    if not contents:
        return None

    return Message(role=role, contents=contents, message_id=msg_id, author_name=author)


def _build_approval_request_content(ii: InterruptIssued) -> Content:
    """Reconstruct a ``function_approval_request`` Content block from an
    ``InterruptIssued`` event."""
    call_id, name, arguments = _read_proposed_call(
        ii.extensions, fallback_call_id=ii.request_id,
        fallback_name=ii.tool_name if ii.HasField("tool_name") else None,
    )
    pair_id = _read_approval_pair_id(ii.extensions) or ii.request_id
    return Content.from_function_approval_request(
        id=pair_id,
        function_call=Content(type="function_call", call_id=call_id, name=name or "", arguments=arguments),
    )


def _build_approval_response_content(
    ir: InterruptResolved,
    issued_by_request_id: dict[str, InterruptIssued],
) -> Content | None:
    """Reconstruct a ``function_approval_response`` Content block from an
    ``InterruptResolved`` event, looking up the matching ``InterruptIssued``
    for proposed-call details when available."""
    matched = issued_by_request_id.get(ir.request_id)
    if matched is not None:
        call_id, name, arguments = _read_proposed_call(
            matched.extensions, fallback_call_id=matched.request_id,
            fallback_name=matched.tool_name if matched.HasField("tool_name") else None,
        )
    else:
        call_id, name, arguments, found = _read_proposed_call_explicit(
            ir.extensions, fallback_call_id=ir.request_id, fallback_name=None,
        )
        if not found:
            # Pathological — no Issued, no proposed_call on the Resolved either.
            logger.debug(
                "Skipping InterruptResolved %s with no matching Issued and no proposed_call.",
                ir.request_id,
            )
            return None

    pair_id = _read_approval_pair_id(ir.extensions) or ir.request_id
    return Content.from_function_approval_response(
        approved=ir.outcome == "allow",
        id=pair_id,
        function_call=Content(type="function_call", call_id=call_id, name=name or "", arguments=arguments),
    )


def _read_proposed_call(
    extensions: Any,
    *,
    fallback_call_id: str,
    fallback_name: str | None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """Read the proposed_call sub-struct, returning fallbacks when absent."""
    call_id, name, arguments, _found = _read_proposed_call_explicit(
        extensions, fallback_call_id=fallback_call_id, fallback_name=fallback_name,
    )
    return call_id, name, arguments


def _extract_arguments(proposed: dict[str, Any]) -> dict[str, Any] | None:
    """Read ``proposed_call.arguments``, preserving an empty dict ``{}`` when present.

    The write path emits ``arguments`` as an empty object for zero-argument calls;
    the naive ``proposed.get("arguments") or None`` collapses ``{}`` to ``None`` and
    breaks round-trip. This helper keeps the empty case distinguishable from "absent".
    """
    if "arguments" not in proposed:
        return None
    args = proposed["arguments"]
    return args if isinstance(args, dict) else None


def _read_proposed_call_explicit(
    extensions: Any,
    *,
    fallback_call_id: str,
    fallback_name: str | None,
) -> tuple[str, str | None, dict[str, Any] | None, bool]:
    """Like ``_read_proposed_call`` but also returns whether the proposed_call
    was actually found in the extensions block. Used by the response-side
    reconstruction to distinguish "fallback used" from "proposed_call present"."""
    if "afw" not in extensions:
        return fallback_call_id, fallback_name, None, False
    afw = MessageToDict(extensions["afw"], preserving_proto_field_name=True)
    proposed = afw.get("interrupt", {}).get("proposed_call")
    if not isinstance(proposed, dict):
        return fallback_call_id, fallback_name, None, False
    return (
        proposed.get("id", fallback_call_id),
        proposed.get("name", fallback_name),
        _extract_arguments(proposed),
        True,
    )


def _read_approval_pair_id(extensions: Any) -> str | None:
    """Extract ``interrupt.approval_pair_id`` from the ``afw`` extension block."""
    if "afw" not in extensions:
        return None
    afw = MessageToDict(extensions["afw"], preserving_proto_field_name=True)
    return afw.get("interrupt", {}).get("approval_pair_id")


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


def _build_interrupt_issued(approval: Any, carrier: Message, timestamp: datetime) -> InterruptIssued:
    fc = approval.function_call
    args = _coerce_arguments(fc.arguments)
    event = InterruptIssued(
        request_id=fc.call_id or "",
        kind="approval",
        tool_name=fc.name or None,
        prompt=_build_approval_prompt(name=fc.name or "", arguments=args),
        message_id=carrier.message_id,
        timestamp=timestamp,
    )
    afw = _build_afw_interrupt_extension(
        call_id=fc.call_id or "",
        name=fc.name or "",
        arguments=args,
        approval_pair_id=approval.id,
    )
    _set_afw_extension(event, afw)
    return event


def _build_interrupt_resolved(response: Any, carrier: Message, timestamp: datetime) -> InterruptResolved:
    fc = response.function_call
    args = _coerce_arguments(fc.arguments)
    event = InterruptResolved(
        request_id=fc.call_id or "",
        outcome="allow" if response.approved else "deny",
        message_id=carrier.message_id,
        timestamp=timestamp,
    )
    afw = _build_afw_interrupt_extension(
        call_id=fc.call_id or "",
        name=fc.name or "",
        arguments=args,
        approval_pair_id=response.id,
    )
    _set_afw_extension(event, afw)
    return event


def _set_afw_extension(event: ProtoMessage, payload: dict[str, Any]) -> None:
    """Populate ``event.extensions["afw"]`` from a plain dict, going through
    the protobuf JSON parser to coerce nested dicts/lists into ``Struct``."""
    from google.protobuf.json_format import ParseDict
    struct = Struct()
    ParseDict(payload, struct)
    event.extensions["afw"].CopyFrom(struct)


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
