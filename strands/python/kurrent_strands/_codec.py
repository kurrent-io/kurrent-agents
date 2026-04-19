"""Strands ``Message`` ↔ canonical event decomposition and reconstruction.

See ``DESIGN.md`` §4 for the mapping rules. Summary:

- ``role=user`` + text → ``UserMessageReceived``
- ``role=user`` + ``toolResult`` → ``ToolResultReceived`` (one per result)
- ``role=assistant`` + text only → ``AssistantTextGenerated``
- ``role=assistant`` + ``toolUse`` (± text) → ``AssistantToolCallsGenerated``

Non-canonical content blocks (image / document / video / reasoning / citations /
cache point / guardContent) plus Strands ``MessageMetadata.custom`` ride
verbatim in ``extensions.strands`` on each emitted canonical event so a
same-framework reader can restore the original ``Message``.

Token usage (``MessageMetadata.usage``) is surfaced separately via
``extract_usage_metadata`` for the ``$usage`` KurrentDB event-metadata channel.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from strands.types.content import Message

from ._schema.events import (
    STRANDS_EXTENSION_KEY,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    _EventBase as CanonicalEvent,
)


# Canonical-content keys in Strands' ContentBlock.
_CANONICAL_BLOCK_KEYS = frozenset({"text", "toolUse", "toolResult"})


def message_to_canonical(
    message: Message,
    *,
    message_index: int,
    timestamp: datetime | None = None,
) -> list[CanonicalEvent]:
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
    non_canonical_blocks: list[dict[str, Any]] = []

    for block in content_blocks:
        if "text" in block and block.get("text") is not None:
            text_chunks.append(block["text"])
        if "toolUse" in block and block.get("toolUse") is not None:
            tool_uses.append(block["toolUse"])
        if "toolResult" in block and block.get("toolResult") is not None:
            tool_results.append(block["toolResult"])
        # Any key outside the canonical set rides in extensions.strands.
        extra = {k: v for k, v in block.items() if k not in _CANONICAL_BLOCK_KEYS}
        if extra:
            non_canonical_blocks.append(extra)

    text_content = "".join(text_chunks) if text_chunks else None
    extensions = _build_strands_extensions(message, non_canonical_blocks)

    results: list[CanonicalEvent] = []

    if role == "user":
        # Tool results come on user-role messages per Strands' model.
        for tr in tool_results:
            # Preserve Strands-specific toolResult fields (notably ``status``
            # — required by Strands' Anthropic adapter) that aren't in the
            # canonical ToolResultReceived shape.
            tr_extras = {
                k: v for k, v in tr.items() if k not in {"toolUseId", "content"}
            }
            per_event_extensions = (
                _merge_extensions(extensions, {"tool_result": tr_extras})
                if tr_extras
                else extensions
            )
            results.append(
                ToolResultReceived(
                    call_id=tr.get("toolUseId") or "",
                    tool_name=None,
                    result=_serialize_tool_result_content(tr.get("content")),
                    message_index=message_index,
                    timestamp=ts,
                    extensions=per_event_extensions,
                )
            )
        if text_content is not None:
            results.append(
                UserMessageReceived(
                    content=text_content,
                    message_index=message_index,
                    timestamp=ts,
                    extensions=extensions,
                )
            )
    else:
        # assistant
        if tool_uses:
            results.append(
                AssistantToolCallsGenerated(
                    tool_calls=[_tool_call_info(tu) for tu in tool_uses],
                    content=text_content,
                    message_index=message_index,
                    timestamp=ts,
                    extensions=extensions,
                )
            )
        elif text_content is not None:
            results.append(
                AssistantTextGenerated(
                    content=text_content,
                    message_index=message_index,
                    timestamp=ts,
                    extensions=extensions,
                )
            )

    return results


def canonical_to_messages(events: list[CanonicalEvent]) -> list[Message]:
    """Reconstruct Strands ``Message``s from an ordered stream of canonical events.

    Events sharing the same ``extensions.strands.message_index`` are merged
    back into one Message (e.g. an ``AssistantToolCallsGenerated`` that has
    accompanying text, plus its original non-canonical content blocks).
    """
    by_index: dict[int, list[CanonicalEvent]] = {}
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


def _build_strands_extensions(
    message: Message, non_canonical_blocks: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
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
    return {STRANDS_EXTENSION_KEY: ext}


def _merge_extensions(
    base: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Shallow-merge ``overrides`` into ``base[STRANDS_EXTENSION_KEY]`` non-destructively."""
    merged = {k: dict(v) for k, v in base.items()}
    strands = merged.setdefault(STRANDS_EXTENSION_KEY, {})
    strands.update(overrides)
    return merged


def _tool_call_info(tool_use: dict[str, Any]) -> ToolCallInfo:
    # Strands uses camelCase (toolUseId, name, input). Canonical uses snake_case
    # (call_id, tool_name, arguments).
    input_value = tool_use.get("input")
    if isinstance(input_value, str):
        try:
            input_value = json.loads(input_value)
        except json.JSONDecodeError:
            input_value = {"_raw": input_value}
    if input_value is not None and not isinstance(input_value, dict):
        input_value = {"_value": input_value}
    return ToolCallInfo(
        call_id=tool_use.get("toolUseId") or "",
        tool_name=tool_use.get("name") or "",
        # Preserve ``{}`` distinctly from missing args.
        arguments=dict(input_value) if input_value is not None else None,
    )


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


def _message_index(event: CanonicalEvent) -> int | None:
    """Return a canonical event's ``message_index`` when it has one.

    Canonical conversation events all carry ``message_index``; framework-
    specific events (StrandsAgentState, etc.) do not.
    """
    return getattr(event, "message_index", None)


def _reconstruct_message(events: list[CanonicalEvent]) -> Message:
    """Merge a group of canonical events sharing a message_index into a Message."""
    role: str = "user"
    content: list[dict[str, Any]] = []
    strands_ext: dict[str, Any] = {}

    for event in events:
        # Extract extension envelope (last one wins — they should all match).
        if event.extensions and STRANDS_EXTENSION_KEY in event.extensions:
            strands_ext = event.extensions[STRANDS_EXTENSION_KEY]

        if isinstance(event, UserMessageReceived):
            role = "user"
            if event.content is not None:
                content.append({"text": event.content})
        elif isinstance(event, AssistantTextGenerated):
            role = "assistant"
            if event.content is not None:
                content.append({"text": event.content})
        elif isinstance(event, AssistantToolCallsGenerated):
            role = "assistant"
            if event.content is not None:
                content.append({"text": event.content})
            for tc in event.tool_calls:
                content.append(
                    {
                        "toolUse": {
                            "toolUseId": tc.call_id,
                            "name": tc.tool_name,
                            "input": tc.arguments if tc.arguments is not None else {},
                        }
                    }
                )
        elif isinstance(event, ToolResultReceived):
            role = "user"
            tr_block: dict[str, Any] = {
                "toolUseId": event.call_id,
                "content": _deserialize_tool_result_content(event.result),
            }
            # Restore Strands-specific fields (e.g. ``status``) from extensions.
            if event.extensions:
                per_event = event.extensions.get(STRANDS_EXTENSION_KEY, {}) or {}
                tr_block.update(per_event.get("tool_result") or {})
            # Default status if missing (e.g. if an ADK-written session is read
            # by Strands): Strands' Anthropic adapter requires it.
            tr_block.setdefault("status", "success")
            content.append({"toolResult": tr_block})

    # Restore non-canonical content blocks (image/document/etc.) at the end.
    for block in strands_ext.get("non_canonical_blocks") or []:
        content.append(block)

    message: Message = {"role": role, "content": content}  # type: ignore[assignment]
    metadata: dict[str, Any] = {}
    if strands_ext.get("custom_metadata"):
        metadata["custom"] = strands_ext["custom_metadata"]
    if strands_ext.get("metrics"):
        metadata["metrics"] = strands_ext["metrics"]
    if metadata:
        message["metadata"] = metadata  # type: ignore[typeddict-item]
    return message


def _deserialize_tool_result_content(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
