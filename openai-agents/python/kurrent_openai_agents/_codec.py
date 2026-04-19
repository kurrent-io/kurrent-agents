"""OpenAI Agents SDK ``TResponseInputItem`` ↔ canonical event mapping.

The SDK's session items are OpenAI Responses API shapes (TypedDicts with a
discriminated ``type`` field). We decompose each item into either a canonical
event (``UserMessageReceived`` / ``AssistantTextGenerated`` /
``AssistantToolCallsGenerated`` / ``ToolResultReceived``) when the shape
maps cleanly, or a framework-specific ``OpenAIItem`` event carrying the raw
dict verbatim when it doesn't.

Every emitted canonical event **also** stashes the original item under
``extensions.openai.raw_item`` — so reconstruction via ``canonical_to_items``
is lossless regardless of whether the item was mapped or verbatim-stored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ._schema.events import (
    OPENAI_EXTENSION_KEY,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    OpenAIItem,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    _EventBase as CanonicalEvent,
)


# OpenAI item types that we decompose onto canonical events. Everything else
# (reasoning, handoff_*, mcp_*, computer_call, shell_call, web_search, etc.)
# rides in OpenAIItem verbatim.
_CANONICAL_ITEM_TYPES = frozenset({"message", "function_call", "function_call_output"})


def items_to_canonical(
    items: list[dict[str, Any]],
    *,
    start_index: int,
    timestamp: datetime | None = None,
) -> list[CanonicalEvent]:
    """Decompose a list of OpenAI session items into canonical events.

    ``start_index`` is the session-wide monotonic counter assigned by the
    caller (the SessionManager keeps track across calls). Each item gets its
    own index; canonical events emitted for one item share an index.
    """
    ts = timestamp or datetime.now(UTC)
    results: list[CanonicalEvent] = []

    for offset, item in enumerate(items):
        message_index = start_index + offset
        kind = item.get("type", "message")
        extensions = {OPENAI_EXTENSION_KEY: {"raw_item": dict(item), "item_type": kind}}

        if kind == "message":
            role = item.get("role", "user")
            content = _extract_message_text(item.get("content"))
            if role == "user":
                results.append(
                    UserMessageReceived(
                        content=content,
                        message_index=message_index,
                        timestamp=ts,
                        extensions=extensions,
                    )
                )
            else:
                # assistant / system — map to AssistantTextGenerated; system
                # prompts are rare on the Responses API input list but fit.
                results.append(
                    AssistantTextGenerated(
                        content=content,
                        message_index=message_index,
                        timestamp=ts,
                        extensions=extensions,
                    )
                )
        elif kind == "function_call":
            # A single tool call item — one canonical AssistantToolCallsGenerated
            # carrying one tool_calls entry.
            results.append(
                AssistantToolCallsGenerated(
                    tool_calls=[
                        ToolCallInfo(
                            call_id=item.get("call_id") or "",
                            tool_name=item.get("name") or "",
                            arguments=_parse_arguments(item.get("arguments")),
                        )
                    ],
                    content=None,
                    message_index=message_index,
                    timestamp=ts,
                    extensions=extensions,
                )
            )
        elif kind == "function_call_output":
            results.append(
                ToolResultReceived(
                    call_id=item.get("call_id") or "",
                    tool_name=None,
                    result=_serialize_output(item.get("output")),
                    message_index=message_index,
                    timestamp=ts,
                    extensions=extensions,
                )
            )
        else:
            # Non-canonical — reasoning, handoff_*, mcp_*, computer_call, etc.
            results.append(
                OpenAIItem(
                    item_type=kind,
                    raw_item=dict(item),
                    message_index=message_index,
                    timestamp=ts,
                )
            )

    return results


def canonical_to_items(events: list[CanonicalEvent]) -> list[dict[str, Any]]:
    """Reconstruct OpenAI session items from an ordered event stream.

    Prefers ``extensions.openai.raw_item`` for lossless reconstruction.
    Falls back to rebuilding from canonical fields when the event carries no
    extension (e.g. cross-framework reads of a session written by ADK).
    """
    items: list[dict[str, Any]] = []
    for event in events:
        # OpenAIItem: raw dict round-trip.
        if isinstance(event, OpenAIItem):
            items.append(dict(event.raw_item))
            continue
        # Canonical events: prefer raw_item if present.
        ext = (event.extensions or {}).get(OPENAI_EXTENSION_KEY) or {}
        raw = ext.get("raw_item")
        if isinstance(raw, dict) and raw:
            items.append(dict(raw))
            continue
        # Fallback reconstruction from canonical fields (cross-framework read).
        item = _fallback_reconstruct(event)
        if item is not None:
            items.append(item)
    return items


# ----- helpers ---------------------------------------------------------------


def _extract_message_text(content: Any) -> str | None:
    """Pull plain text out of a Responses API ``content`` field.

    ``content`` is either a string or a list of typed content parts
    (``{"type": "input_text", "text": "..."}`` etc.). For non-text parts we
    concatenate only the text; richer content rides verbatim via ``raw_item``.
    """
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
    """Parse a ``function_call.arguments`` blob into a dict.

    OpenAI returns JSON-encoded strings; we preserve empty-dict args distinctly
    from missing args (Anthropic and some other readers reject ``null``).
    """
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


def _fallback_reconstruct(event: CanonicalEvent) -> dict[str, Any] | None:
    """Best-effort item rebuild when no ``raw_item`` is present.

    Used only for cross-framework reads (e.g. an ADK agent wrote the session
    and we're reading it from OpenAI's side). Generates a minimal Responses
    API item that's good enough for the SDK to append to and continue.
    """
    if isinstance(event, UserMessageReceived):
        return {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": event.content or ""}],
        }
    if isinstance(event, AssistantTextGenerated):
        return {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": event.content or ""}],
        }
    if isinstance(event, AssistantToolCallsGenerated):
        # OpenAI uses one ``function_call`` item per tool call. If we have
        # multiple tool_calls on one canonical event (common for ADK), emit
        # only the first here — best-effort. Real-world cross-framework
        # reads should always have raw_item present.
        if not event.tool_calls:
            return None
        tc = event.tool_calls[0]
        return {
            "type": "function_call",
            "call_id": tc.call_id,
            "name": tc.tool_name,
            "arguments": json.dumps(tc.arguments if tc.arguments is not None else {}),
        }
    if isinstance(event, ToolResultReceived):
        return {
            "type": "function_call_output",
            "call_id": event.call_id,
            "output": event.result,
        }
    return None
