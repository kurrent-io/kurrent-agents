"""Strands ``Message`` ↔ canonical event decomposition and reconstruction.

See ``DESIGN.md`` §4 for the mapping rules. Summary:

- ``role=user`` + text → ``UserMessageReceived``
- ``role=user`` + ``toolResult`` → ``ToolResultReceived`` (one per result)
- ``role=assistant`` + text only → ``AssistantTextGenerated``
- ``role=assistant`` + ``toolUse`` (± text) → ``AssistantToolCallsGenerated``
- ``role=assistant`` + ``reasoningContent`` → ``AssistantThinkingGenerated``
  (new in v2; emitted before the text/tool event for the same message turn)

Non-canonical content blocks (image / document / video / citations / cache
point / guardContent) plus Strands ``MessageMetadata.custom`` ride verbatim
in ``extensions.strands`` on each emitted canonical event so a same-framework
reader can restore the original ``Message``.

Token usage (``MessageMetadata.usage``) is surfaced separately via
``extract_usage_metadata`` for the ``$usage`` KurrentDB event-metadata channel.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import UTC, datetime
from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtoMessage
from google.protobuf.struct_pb2 import Struct
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)
from strands.types.content import Message

logger = logging.getLogger("kurrent_strands._codec")

STRANDS_EXTENSION_KEY: str = "strands"
"""Slug under which Strands-specific fields ride on canonical events'
``extensions`` map. See ``schema/SCHEMA_v2.md §5``."""

_BYTES_MARKER: str = "__bytes_b64__"
"""Wrapper key used inside ``extensions.strands.*`` payloads to round-trip
``bytes`` values losslessly through ``google.protobuf.Struct`` (which is
JSON-shaped and cannot hold raw bytes). A bytes value is replaced with
``{_BYTES_MARKER: <base64-string>}`` on write, and decoded back on read."""

# Canonical-content keys in Strands' ContentBlock — these decompose into
# canonical events. Anything else rides in
# ``extensions.strands.non_canonical_blocks``.
_CANONICAL_BLOCK_KEYS = frozenset({"text", "toolUse", "toolResult", "reasoningContent"})


def message_to_canonical(
    message: Message,
    *,
    message_index: int,
    timestamp: datetime | None = None,
) -> list[ProtoMessage]:
    """Decompose a Strands ``Message`` into one or more canonical events.

    ``message_index`` is assigned by the caller (the session manager keeps a
    monotonic counter per session).
    """
    ts = timestamp or datetime.now(UTC)
    content_blocks = message.get("content") or []
    role = message.get("role", "user")

    text_chunks: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    reasoning_blocks: list[dict[str, Any]] = []
    non_canonical_blocks: list[dict[str, Any]] = []

    for block in content_blocks:
        if "text" in block and block.get("text") is not None:
            text_chunks.append(block["text"])
        if "toolUse" in block and block.get("toolUse") is not None:
            tool_uses.append(block["toolUse"])
        if "toolResult" in block and block.get("toolResult") is not None:
            tool_results.append(block["toolResult"])
        if "reasoningContent" in block and block.get("reasoningContent") is not None:
            reasoning_blocks.append(block["reasoningContent"])
        # Anything outside the canonical set rides in extensions.strands.
        extra = {k: v for k, v in block.items() if k not in _CANONICAL_BLOCK_KEYS}
        if extra:
            non_canonical_blocks.append(extra)

    text_content = "".join(text_chunks) if text_chunks else None
    base_strands_ext = _build_strands_extensions_dict(message, non_canonical_blocks)

    results: list[ProtoMessage] = []

    if role == "user":
        # Tool results come on user-role messages per Strands' model.
        for tr in tool_results:
            tr_extras = {
                k: v for k, v in tr.items() if k not in {"toolUseId", "content"}
            }
            ext_for_event = (
                _merge_strands_extension(base_strands_ext, "tool_result", tr_extras)
                if tr_extras
                else base_strands_ext
            )
            evt = ToolResultReceived(
                call_id=tr.get("toolUseId") or "",
                result=_serialize_tool_result_content(tr.get("content")),
                message_index=message_index,
                timestamp=ts,
            )
            _set_strands_extension(evt, ext_for_event)
            results.append(evt)
        if text_content is not None:
            evt = UserMessageReceived(
                content=text_content,
                message_index=message_index,
                timestamp=ts,
            )
            _set_strands_extension(evt, base_strands_ext)
            results.append(evt)
        return results

    # role == "assistant"
    # Thinking events come FIRST in the message turn (per SCHEMA_v2 §3.2 ordering).
    for rc in reasoning_blocks:
        results.append(_build_thinking_event(rc, message_index, ts, base_strands_ext))

    if tool_uses:
        evt = AssistantToolCallsGenerated(
            tool_calls=[_tool_call_info(tu) for tu in tool_uses],
            content=text_content,
            message_index=message_index,
            timestamp=ts,
        )
        _set_strands_extension(evt, base_strands_ext)
        results.append(evt)
    elif text_content is not None:
        evt = AssistantTextGenerated(
            content=text_content,
            message_index=message_index,
            timestamp=ts,
        )
        _set_strands_extension(evt, base_strands_ext)
        results.append(evt)

    return results


def canonical_to_messages(events: list[ProtoMessage]) -> list[Message]:
    """Reconstruct Strands ``Message``s from an ordered stream of canonical events.

    Events sharing the same ``message_index`` are merged back into one Message
    (e.g. an ``AssistantToolCallsGenerated`` that has accompanying text, plus
    its original non-canonical content blocks).
    """
    by_index: dict[int, list[ProtoMessage]] = {}
    order: list[int] = []
    for event in events:
        idx = _message_index(event)
        if idx is None:
            continue
        if idx not in by_index:
            by_index[idx] = []
            order.append(idx)
        by_index[idx].append(event)

    return [_reconstruct_message(by_index[i]) for i in order]


def extract_usage_metadata(message: Message) -> dict[str, Any] | None:
    """Build the ``$usage`` KurrentDB event-metadata payload from a Message.

    Returns ``None`` when the message has no ``metadata.usage`` (Strands'
    Usage TypedDict is camelCase; canonical ``$usage`` is snake_case).

    Strands ``Usage`` does not surface a ``reasoning_tokens`` field — those
    counts are folded into ``outputTokens`` upstream, so canonical
    ``$usage.reasoning_tokens`` stays absent on Strands-emitted events.
    """
    metadata = message.get("metadata")
    if not metadata:
        return None
    usage = metadata.get("usage")
    if not usage:
        return None
    payload: dict[str, Any] = {}
    for src, dst in (
        ("inputTokens", "input_tokens"),
        ("outputTokens", "output_tokens"),
        ("totalTokens", "total_tokens"),
        ("cacheReadInputTokens", "cached_input_tokens"),
        ("cacheWriteInputTokens", "cache_write_input_tokens"),
    ):
        value = usage.get(src)
        if value is not None:
            payload[dst] = int(value)
    return payload or None


# ----- helpers ---------------------------------------------------------------


def _build_strands_extensions_dict(
    message: Message, non_canonical_blocks: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the ``extensions.strands`` payload as a plain dict.

    Returned dict is later converted to a ``google.protobuf.Struct`` via
    :func:`_set_strands_extension` when stamped on an event.
    """
    ext: dict[str, Any] = {}
    metadata = message.get("metadata")
    if metadata:
        custom = metadata.get("custom")
        if custom:
            ext["custom_metadata"] = dict(custom)
        metrics = metadata.get("metrics")
        if metrics:
            ext["metrics"] = dict(metrics)
    if non_canonical_blocks:
        ext["non_canonical_blocks"] = non_canonical_blocks
    return ext


def _merge_strands_extension(
    base: dict[str, Any], key: str, value: Any
) -> dict[str, Any]:
    """Return a copy of ``base`` with ``key`` set to ``value``."""
    merged = dict(base)
    merged[key] = value
    return merged


def _set_strands_extension(event: ProtoMessage, payload: dict[str, Any]) -> None:
    """Stamp ``event.extensions['strands']`` from a plain dict.

    No-op when ``payload`` is empty so we don't emit a present-but-empty
    extension entry on the wire. Recursively wraps ``bytes`` values via
    :data:`_BYTES_MARKER` because ``google.protobuf.Struct`` is JSON-shaped
    and cannot hold raw bytes. The pre-migration Pydantic codec used
    ``ser_json_bytes="base64"`` to do the same; this restores that behaviour
    so non-canonical content blocks with binary payloads round-trip cleanly.
    """
    if not payload:
        return
    struct = Struct()
    ParseDict(_jsonify_for_struct(payload), struct)
    event.extensions[STRANDS_EXTENSION_KEY].CopyFrom(struct)


def _read_strands_extension(event: ProtoMessage) -> dict[str, Any]:
    """Read ``event.extensions['strands']`` back as a plain dict.

    Returns an empty dict when the slug is absent — proto map fields are
    always present, so we check membership explicitly. ``bytes`` values
    wrapped on write via :data:`_BYTES_MARKER` are unwrapped here.
    """
    if STRANDS_EXTENSION_KEY not in event.extensions:
        return {}
    raw = MessageToDict(
        event.extensions[STRANDS_EXTENSION_KEY], preserving_proto_field_name=True
    )
    return _dejsonify_from_struct(raw)


def _jsonify_for_struct(value: Any) -> Any:
    """Recursively coerce a Python value into a Struct-compatible JSON shape.

    ``bytes`` are wrapped as ``{_BYTES_MARKER: <base64>}`` so they round-trip
    losslessly via :func:`_dejsonify_from_struct`. Non-JSON-native scalars
    (e.g. ``datetime``) are stringified — they are best-effort metadata, not
    the canonical wire path.
    """
    if isinstance(value, bytes):
        return {_BYTES_MARKER: base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {k: _jsonify_for_struct(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify_for_struct(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _dejsonify_from_struct(value: Any) -> Any:
    """Inverse of :func:`_jsonify_for_struct` — unwrap ``_BYTES_MARKER`` dicts.

    A wrapper dict with malformed base64 falls back to the wrapper itself so
    the rest of the extension payload keeps round-tripping.
    """
    if isinstance(value, dict):
        if list(value.keys()) == [_BYTES_MARKER]:
            try:
                return base64.b64decode(value[_BYTES_MARKER], validate=True)
            except (binascii.Error, TypeError, ValueError):
                logger.warning(
                    "Skipping malformed base64 in extensions.strands "
                    "(%s wrapper); leaving raw value in place.",
                    _BYTES_MARKER,
                )
                return value
        return {k: _dejsonify_from_struct(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dejsonify_from_struct(v) for v in value]
    return value


def _build_thinking_event(
    reasoning_content: dict[str, Any],
    message_index: int,
    timestamp: datetime,
    base_strands_ext: dict[str, Any],
) -> AssistantThinkingGenerated:
    """Build an ``AssistantThinkingGenerated`` from a ``reasoningContent`` block.

    Strands reasoning is plaintext (``encrypted`` stays at the proto default
    of ``False``). The optional ``signature`` is a canonical field on the
    event (``SCHEMA_v2.md §3.2``); any ``redactedContent`` bytes ride in
    ``extensions.strands.thinking.redacted_content`` as a base64 string.
    """
    reasoning_text = reasoning_content.get("reasoningText") or {}
    text = reasoning_text.get("text")
    signature = reasoning_text.get("signature")
    redacted = reasoning_content.get("redactedContent")

    evt = AssistantThinkingGenerated(
        content=text,
        message_index=message_index,
        timestamp=timestamp,
    )
    if signature:
        evt.signature = signature

    if redacted is not None:
        ext_for_event = _merge_strands_extension(
            base_strands_ext,
            "thinking",
            {"redacted_content": base64.b64encode(redacted).decode("ascii")},
        )
    else:
        ext_for_event = base_strands_ext

    _set_strands_extension(evt, ext_for_event)
    return evt


def coerce_tool_input(input_value: Any) -> dict[str, Any] | None:
    """Coerce a Strands ``ToolUse.input`` value to a JSON-shaped dict.

    Strands tool inputs are typed ``Any`` and observed as: dict (the common
    case), JSON-string (some adapters re-encode), or scalar (provider quirks).
    We normalise to a dict so canonical ``ToolCallInfo.arguments`` (a
    ``google.protobuf.Struct``) and ``extensions.strands.interrupt.proposed_call``
    can hold the value uniformly without the caller worrying about shape.

    Returns ``None`` when ``input_value`` is itself ``None`` so the caller
    can distinguish "absent" from "empty dict".
    """
    if input_value is None:
        return None
    if isinstance(input_value, str):
        try:
            parsed = json.loads(input_value)
        except json.JSONDecodeError:
            return {"_raw": input_value}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    if isinstance(input_value, dict):
        return input_value
    return {"_value": input_value}


def _tool_call_info(tool_use: dict[str, Any]) -> ToolCallInfo:
    """Convert a Strands toolUse block to a canonical ``ToolCallInfo``.

    Strands uses camelCase (``toolUseId``, ``name``, ``input``); canonical
    uses snake_case (``call_id``, ``tool_name``, ``arguments``). The
    ``arguments`` field is a ``google.protobuf.Struct`` — empty dicts must
    survive round-trip (see schema commit ``ff1540d``).
    """
    input_value = coerce_tool_input(tool_use.get("input"))

    info = ToolCallInfo(
        call_id=tool_use.get("toolUseId") or "",
        tool_name=tool_use.get("name") or "",
    )
    if input_value is not None:
        # ``Struct.update`` preserves the empty-dict case (Struct is "present
        # but empty"), distinct from "absent" which we don't emit.
        info.arguments.update(input_value)
    return info


def _serialize_tool_result_content(content: Any) -> str | None:
    """Serialize a Strands ``ToolResult.content`` to a string for canonical storage.

    Strands tool results carry a list of content blocks (text, json, image, …).
    Canonical ``ToolResultReceived.result`` is a string; we stringify as JSON
    so everything round-trips losslessly via the Strands extension block.
    """
    if content is None:
        return None
    try:
        return json.dumps(content)
    except (TypeError, ValueError):
        return json.dumps(content, default=str)


def _message_index(event: ProtoMessage) -> int | None:
    """Return a canonical event's ``message_index`` when it has one.

    Lifecycle events (``SessionStarted`` / ``SessionEnded``) and Strands-
    specific events lack the field; we skip them on reconstruction.
    """
    try:
        if not event.HasField("message_index"):
            return None
    except (AttributeError, ValueError):
        return None
    return event.message_index


def _reconstruct_message(events: list[ProtoMessage]) -> Message:
    """Merge a group of canonical events sharing a ``message_index`` into a Message."""
    role: str = "user"
    content: list[dict[str, Any]] = []
    base_strands_ext: dict[str, Any] = {}

    for event in events:
        per_event_ext = _read_strands_extension(event)
        # Track the last seen "shared" extension fields (custom_metadata, metrics,
        # non_canonical_blocks). Per-event-only fields like ``tool_result`` and
        # ``thinking`` are read inline below and not promoted to the shared dict.
        for k in ("custom_metadata", "metrics", "non_canonical_blocks"):
            if k in per_event_ext:
                base_strands_ext[k] = per_event_ext[k]

        if isinstance(event, UserMessageReceived):
            role = "user"
            if event.HasField("content"):
                content.append({"text": event.content})
        elif isinstance(event, AssistantTextGenerated):
            role = "assistant"
            if event.HasField("content"):
                content.append({"text": event.content})
        elif isinstance(event, AssistantThinkingGenerated):
            role = "assistant"
            content.append(_reconstruct_reasoning_block(event, per_event_ext))
        elif isinstance(event, AssistantToolCallsGenerated):
            role = "assistant"
            if event.HasField("content"):
                content.append({"text": event.content})
            for tc in event.tool_calls:
                tu_block: dict[str, Any] = {
                    "toolUseId": tc.call_id,
                    "name": tc.tool_name,
                    "input": _struct_to_dict(tc.arguments) if tc.HasField("arguments") else {},
                }
                content.append({"toolUse": tu_block})
        elif isinstance(event, ToolResultReceived):
            role = "user"
            tr_block: dict[str, Any] = {
                "toolUseId": event.call_id,
                "content": _normalise_tool_result_content(
                    _deserialize_tool_result_content(event.result)
                ),
            }
            tr_extras = per_event_ext.get("tool_result")
            if isinstance(tr_extras, dict):
                tr_block.update(tr_extras)
            # Strands' Anthropic adapter requires ``status``; default to success
            # when missing (e.g. cross-framework reads from non-Strands writers).
            tr_block.setdefault("status", "success")
            content.append({"toolResult": tr_block})

    # Restore non-canonical content blocks (image / document / etc.) at the end.
    for block in base_strands_ext.get("non_canonical_blocks") or []:
        content.append(block)

    message: Message = {"role": role, "content": content}  # type: ignore[assignment]
    metadata: dict[str, Any] = {}
    if base_strands_ext.get("custom_metadata"):
        metadata["custom"] = base_strands_ext["custom_metadata"]
    if base_strands_ext.get("metrics"):
        metadata["metrics"] = base_strands_ext["metrics"]
    if metadata:
        message["metadata"] = metadata  # type: ignore[typeddict-item]
    return message


def _reconstruct_reasoning_block(
    event: AssistantThinkingGenerated, strands_ext: dict[str, Any]
) -> dict[str, Any]:
    """Rebuild a Strands ``reasoningContent`` block from a thinking event.

    ``signature`` is read from the canonical event field (``SCHEMA_v2.md §3.2``);
    ``redactedContent`` is decoded from ``extensions.strands.thinking.redacted_content``.
    A malformed base64 value is logged and skipped so a single corrupt event
    does not break session restore.
    """
    rc: dict[str, Any] = {}
    rt: dict[str, Any] = {}
    if event.HasField("content"):
        rt["text"] = event.content
    if event.HasField("signature"):
        rt["signature"] = event.signature
    if rt:
        rc["reasoningText"] = rt

    thinking = strands_ext.get("thinking") or {}
    redacted_b64 = thinking.get("redacted_content")
    if isinstance(redacted_b64, str):
        try:
            rc["redactedContent"] = base64.b64decode(redacted_b64, validate=True)
        except (binascii.Error, TypeError, ValueError):
            logger.warning(
                "Skipping malformed redacted_content base64 on "
                "AssistantThinkingGenerated; reconstructed reasoning block "
                "will omit redactedContent."
            )
    return {"reasoningContent": rc}


def _struct_to_dict(struct: Struct) -> dict[str, Any]:
    """Convert a ``google.protobuf.Struct`` to a plain dict, preserving keys."""
    return MessageToDict(struct, preserving_proto_field_name=True)


def _deserialize_tool_result_content(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


# Keys that identify a Strands content block. Mirrors
# ``strands.types.content.ContentBlock`` (a total=False TypedDict). A
# tool-result content list is "already Strands-shaped" only when every
# element is a dict carrying one of these.
_STRANDS_CONTENT_BLOCK_KEYS = frozenset(
    {
        "text",
        "json",
        "image",
        "document",
        "video",
        "toolUse",
        "toolResult",
        "reasoningContent",
        "guardContent",
        "cachePoint",
    }
)


def _is_strands_content_block(value: Any) -> bool:
    """True when ``value`` looks like a Strands content block — a dict
    carrying at least one recognised content-block key (``text``,
    ``json``, ``image``, …)."""
    return isinstance(value, dict) and any(
        key in _STRANDS_CONTENT_BLOCK_KEYS for key in value
    )


def _normalise_tool_result_content(value: Any) -> list[dict[str, Any]]:
    """Coerce a deserialised ``ToolResultReceived.result`` payload into the
    list-of-content-blocks shape Strands' model adapters require.

    Strands writers serialise ``ToolResult.content`` as a JSON-encoded list
    of content blocks (``[{"text": ...}, {"json": ...}, ...]``); reading
    back is lossless. Other framework writers (e.g. MAF Python's
    ``KurrentDBHistoryProvider``) write the raw tool return —
    ``json.dumps`` of *any* non-string value, so a dict, a string, a
    number, OR an array — directly into the canonical
    ``ToolResultReceived.result`` field. When Strands then reads such a
    session, ``toolResult.content`` ends up as a non-block shape, and
    Strands' model adapters (e.g. ``AnthropicModel.format_request``)
    raise ``TypeError: content_type=<...> | unsupported type`` because
    they iterate ``content`` expecting block dicts.

    Normalise so cross-framework reads don't crash. A ``list`` is trusted
    *only* when every element is a recognised Strands content block — a
    MAF tool that returned ``[{"status": "ok"}]`` serialises to a list
    whose element is **not** a content block, so that whole list is
    wrapped rather than trusted:

    * ``None`` → ``[]``
    * ``list`` of all content blocks (incl. empty) → trusted as-is
    * ``list`` with any non-block element → wrapped as ``[{"json": value}]``
    * ``dict`` that is itself a content block → wrapped as ``[value]``
    * any other ``dict`` → wrapped as ``[{"json": value}]``
    * any primitive (str/int/bool/...) → wrapped as ``[{"text": str(value)}]``

    See https://github.com/kurrent-io/kurrent-agents/issues/58.
    """
    if value is None:
        return []
    if isinstance(value, list):
        # `all()` is True for the empty list — an empty tool-result
        # content list round-trips unchanged.
        if all(_is_strands_content_block(el) for el in value):
            return value
        return [{"json": value}]
    if isinstance(value, dict):
        if _is_strands_content_block(value):
            return [value]
        return [{"json": value}]
    return [{"text": str(value)}]
