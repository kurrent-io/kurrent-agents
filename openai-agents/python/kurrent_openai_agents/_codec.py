"""OpenAI Agents SDK ``TResponseInputItem`` ↔ canonical event mapping.

The SDK's session items are OpenAI Responses API shapes (TypedDicts with a
discriminated ``type`` field). We decompose each item into either a canonical
event (``UserMessageReceived`` / ``AssistantTextGenerated`` /
``AssistantToolCallsGenerated`` / ``ToolResultReceived`` /
``AssistantThinkingGenerated`` / ``InterruptIssued`` / ``InterruptResolved``)
when the shape maps cleanly, or a framework-specific ``OpenAIItem`` event
carrying the raw dict verbatim when it doesn't.

Every emitted canonical event also stashes the original item under
``extensions.openai.raw_item`` — so reconstruction via
``canonical_to_items`` is lossless regardless of whether the item was
mapped or verbatim-stored.
"""

from __future__ import annotations

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
    InterruptIssued,
    InterruptResolved,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)

from ._openai_events import OPENAI_EXTENSION_KEY, OpenAIItem

logger = logging.getLogger("kurrent_openai_agents._codec")


# OpenAI item types that we decompose onto canonical events. Tasks 6 and 7
# extend this set.
_CANONICAL_ITEM_TYPES = frozenset({
    "message",
    "function_call",
    "function_call_output",
    "reasoning",
    "mcp_approval_request",
    "mcp_approval_response",
})


def items_to_canonical(
    items: list[dict[str, Any]],
    *,
    start_index: int,
    timestamp: datetime | None = None,
) -> list[ProtoMessage | OpenAIItem]:
    """Decompose a list of OpenAI session items into canonical events.

    ``start_index`` is the session-wide monotonic counter assigned by the
    caller. Each item gets its own index; canonical events emitted for one
    item share that index.

    Naive ``timestamp`` arguments are interpreted as UTC, matching the
    default ``datetime.now(UTC)`` and ``Timestamp.FromDatetime`` semantics.
    Tz-aware datetimes are converted to UTC. Either way, mappers downstream
    receive a tz-aware UTC datetime, so they cannot accidentally invoke
    ``datetime.astimezone(UTC)`` on a naive value (which would silently
    interpret it as local time on Python 3.11+).
    """
    ts = timestamp or datetime.now(UTC)
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    results: list[ProtoMessage | OpenAIItem] = []

    for offset, item in enumerate(items):
        message_index = start_index + offset
        kind = item.get("type", "message")

        if kind == "message":
            results.extend(_map_message(item, message_index, ts))
        elif kind == "function_call":
            results.append(_map_function_call(item, message_index, ts))
        elif kind == "function_call_output":
            results.append(_map_function_call_output(item, message_index, ts))
        elif kind == "reasoning":
            results.append(_map_reasoning(item, message_index, ts))
        elif kind == "mcp_approval_request":
            results.append(_map_mcp_approval_request(item, message_index, ts))
        elif kind == "mcp_approval_response":
            results.append(_map_mcp_approval_response(item, message_index, ts))
        else:
            # Non-canonical — handoff_*, computer_call, shell_call, web_search,
            # etc. Reasoning + MCP approvals are added in tasks 6 and 7.
            results.append(_wrap_openai_item(item, kind, message_index, ts))

    return results


def canonical_to_items(events: list[ProtoMessage | OpenAIItem]) -> list[dict[str, Any]]:
    """Reconstruct OpenAI session items from an ordered event stream.

    Prefers ``extensions.openai.raw_item`` for lossless reconstruction.
    Falls back to rebuilding from canonical fields when the event carries no
    extension (e.g. cross-framework reads of a session written by ADK).
    """
    items: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, OpenAIItem):
            items.append(dict(event.raw_item))
            continue
        ext = _read_openai_extension(event)
        raw = ext.get("raw_item") if isinstance(ext, dict) else None
        if isinstance(raw, dict) and raw:
            items.append(dict(raw))
            continue
        item = _fallback_reconstruct(event)
        if item is not None:
            items.append(item)
    return items


# ----- per-item-type mappers -------------------------------------------------


def _map_message(
    item: dict[str, Any], message_index: int, ts: datetime
) -> list[ProtoMessage]:
    role = item.get("role", "user")
    content = _extract_message_text(item.get("content"))

    if role == "user":
        evt = UserMessageReceived(message_index=message_index)
    else:
        # Assistant / system — map to AssistantTextGenerated.
        evt = AssistantTextGenerated(message_index=message_index)

    if content is not None:
        evt.content = content
    evt.timestamp.FromDatetime(ts.replace(tzinfo=None))
    _set_openai_extension(evt, {"raw_item": dict(item), "item_type": item.get("type", "message")})
    return [evt]


def _map_function_call(
    item: dict[str, Any], message_index: int, ts: datetime
) -> AssistantToolCallsGenerated:
    evt = AssistantToolCallsGenerated(message_index=message_index)
    evt.timestamp.FromDatetime(ts.replace(tzinfo=None))

    tc = ToolCallInfo()
    tc.call_id = item.get("call_id") or ""
    tc.tool_name = item.get("name") or ""
    args = _parse_arguments(item.get("arguments"))
    if args is not None:
        # Empty-dict args are preserved by design (schema commit ff1540d).
        # MergeFrom with a Struct (even empty) marks the field as present;
        # plain update({}) does not set the has-bit.
        s = Struct()
        s.update(args)
        tc.arguments.MergeFrom(s)
    evt.tool_calls.append(tc)

    _set_openai_extension(evt, {"raw_item": dict(item), "item_type": "function_call"})
    return evt


def _map_function_call_output(
    item: dict[str, Any], message_index: int, ts: datetime
) -> ToolResultReceived:
    evt = ToolResultReceived(
        call_id=item.get("call_id") or "",
        message_index=message_index,
    )
    result = _serialize_output(item.get("output"))
    if result is not None:
        evt.result = result
    evt.timestamp.FromDatetime(ts.replace(tzinfo=None))
    _set_openai_extension(evt, {"raw_item": dict(item), "item_type": "function_call_output"})
    return evt


def _wrap_openai_item(
    item: dict[str, Any], kind: str, message_index: int, ts: datetime
) -> OpenAIItem:
    return OpenAIItem(
        item_type=kind,
        raw_item=dict(item),
        message_index=message_index,
        timestamp=ts,
    )


def _map_reasoning(
    item: dict[str, Any], message_index: int, ts: datetime
) -> AssistantThinkingGenerated:
    """Map an OpenAI ``reasoning`` item to ``AssistantThinkingGenerated``.

    Two shapes per SCHEMA_v2 §3.2:
    - Plaintext: ``content[*].text`` carries reasoning text; ``encrypted=False``.
    - Encrypted (o-series): opaque ``encrypted_content`` + optional ``signature``;
      blob rides under ``extensions.openai.thinking.raw``.
    """
    evt = AssistantThinkingGenerated(message_index=message_index)
    evt.timestamp.FromDatetime(ts.replace(tzinfo=None))

    text = _extract_reasoning_text(item.get("content") or item.get("summary"))
    encrypted_blob = item.get("encrypted_content")
    signature = item.get("signature")

    ext_payload: dict[str, Any] = {"raw_item": dict(item), "item_type": "reasoning"}

    if encrypted_blob is not None:
        # Only set the field when True. Edition 2024 emits any explicitly-set
        # field on the wire (`"encrypted": false` would drift from the canonical
        # fixture, which omits the key when reasoning is plaintext).
        evt.encrypted = True
        if signature:
            evt.signature = signature
        ext_payload["thinking"] = {"raw": encrypted_blob}
    else:
        if text is not None:
            evt.content = text
        if signature:
            evt.signature = signature

    _set_openai_extension(evt, ext_payload)
    return evt


def _map_mcp_approval_request(
    item: dict[str, Any], message_index: int, ts: datetime
) -> InterruptIssued:
    """Map an MCP ``mcp_approval_request`` item to ``InterruptIssued``.

    Post-hoc gate per SCHEMA_v2 §3.3: ``request_id`` equals the gated tool
    call's ``call_id``. The proposed call rides under
    ``extensions.openai.interrupt.proposed_call`` per the documented soft
    convention.
    """
    request_id = item.get("id") or ""
    name = item.get("name") or ""
    args = _parse_arguments(item.get("arguments"))

    evt = InterruptIssued(
        request_id=request_id,
        kind="approval",
    )
    if name:
        evt.tool_name = name
    evt.timestamp.FromDatetime(ts.replace(tzinfo=None))

    proposed_call: dict[str, Any] = {
        "id": request_id,
        "name": name,
        "arguments": args if args is not None else {},
    }
    _set_openai_extension(evt, {
        "raw_item": dict(item),
        "item_type": "mcp_approval_request",
        "interrupt": {"proposed_call": proposed_call},
    })
    return evt


def _map_mcp_approval_response(
    item: dict[str, Any], message_index: int, ts: datetime
) -> InterruptResolved:
    """Map ``mcp_approval_response`` to ``InterruptResolved``.

    ``approve=True`` ⇒ ``outcome=allow``; ``approve=False`` ⇒ ``outcome=deny``.
    Free-text rationale (when present) goes in canonical ``response``.
    """
    evt = InterruptResolved(
        request_id=item.get("approval_request_id") or "",
        outcome="allow" if item.get("approve") else "deny",
    )
    reason = item.get("reason")
    if isinstance(reason, str) and reason:
        evt.response = reason
    evt.timestamp.FromDatetime(ts.replace(tzinfo=None))
    _set_openai_extension(evt, {
        "raw_item": dict(item),
        "item_type": "mcp_approval_response",
    })
    return evt


def _extract_reasoning_text(blocks: Any) -> str | None:
    """Pull plaintext from a ``reasoning.content`` or ``reasoning.summary`` list.

    Handles ``{"type": "reasoning_text", "text": "..."}`` and
    ``{"type": "summary_text", "text": "..."}`` entries; concatenates text.
    """
    if not isinstance(blocks, list):
        return None
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"reasoning_text", "summary_text", "text"}:
            value = block.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts) or None


# ----- extension helpers -----------------------------------------------------


def _set_openai_extension(event: ProtoMessage, payload: dict[str, Any]) -> None:
    """Stamp ``event.extensions['openai']`` from a plain dict.

    Goes through :func:`google.protobuf.json_format.ParseDict` to coerce
    nested dicts/lists into ``Struct``. No bytes handling is needed because
    OpenAI Responses items are JSON-shaped (binary content is base64'd
    server-side).
    """
    if not payload:
        return
    struct = Struct()
    ParseDict(payload, struct)
    event.extensions[OPENAI_EXTENSION_KEY].CopyFrom(struct)


def _read_openai_extension(event: ProtoMessage) -> dict[str, Any]:
    """Read ``event.extensions['openai']`` back as a plain dict."""
    if not hasattr(event, "extensions") or OPENAI_EXTENSION_KEY not in event.extensions:
        return {}
    return MessageToDict(
        event.extensions[OPENAI_EXTENSION_KEY], preserving_proto_field_name=True
    )


# ----- text + argument coercion ---------------------------------------------


def _extract_message_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"input_text", "output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "".join(texts) or None
    return None


def _parse_arguments(arguments: Any) -> dict[str, Any] | None:
    if arguments is None:
        return None
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"_raw": arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    return {"_value": arguments}


def _serialize_output(output: Any) -> str | None:
    if output is None:
        return None
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output)
    except (TypeError, ValueError):
        return json.dumps(output, default=str)


# ----- cross-framework fallback ---------------------------------------------


def _fallback_reconstruct(event: ProtoMessage) -> dict[str, Any] | None:
    """Best-effort item rebuild when no ``raw_item`` is present.

    Used only for cross-framework reads (e.g. an ADK agent wrote the session
    and we're reading it from OpenAI's side).
    """
    if isinstance(event, UserMessageReceived):
        return {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": event.content if event.HasField("content") else ""}],
        }
    if isinstance(event, AssistantTextGenerated):
        return {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": event.content if event.HasField("content") else ""}],
        }
    if isinstance(event, AssistantToolCallsGenerated):
        if not event.tool_calls:
            return None
        tc = event.tool_calls[0]
        args = MessageToDict(tc.arguments, preserving_proto_field_name=True) if tc.HasField("arguments") else {}
        return {
            "type": "function_call",
            "call_id": tc.call_id,
            "name": tc.tool_name,
            "arguments": json.dumps(args),
        }
    if isinstance(event, ToolResultReceived):
        return {
            "type": "function_call_output",
            "call_id": event.call_id,
            "output": event.result if event.HasField("result") else None,
        }
    if isinstance(event, AssistantThinkingGenerated):
        if event.encrypted:
            ext = _read_openai_extension(event)
            blob = ext.get("thinking", {}).get("raw") if isinstance(ext, dict) else None
            item: dict[str, Any] = {"type": "reasoning"}
            if blob is not None:
                item["encrypted_content"] = blob
            if event.HasField("signature"):
                item["signature"] = event.signature
            return item
        text = event.content if event.HasField("content") else ""
        return {
            "type": "reasoning",
            "content": [{"type": "reasoning_text", "text": text}],
        }
    if isinstance(event, InterruptIssued):
        ext = _read_openai_extension(event)
        proposed = (ext.get("interrupt") or {}).get("proposed_call") or {}
        return {
            "type": "mcp_approval_request",
            "id": event.request_id,
            "name": event.tool_name if event.HasField("tool_name") else proposed.get("name", ""),
            "arguments": json.dumps(proposed.get("arguments") or {}),
        }
    if isinstance(event, InterruptResolved):
        resolved_item: dict[str, Any] = {
            "type": "mcp_approval_response",
            "approval_request_id": event.request_id,
            "approve": event.outcome == "allow",
        }
        if event.HasField("response"):
            resolved_item["reason"] = event.response
        return resolved_item
    return None
