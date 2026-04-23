"""Canonical decomposition of ``ClaudeSDKEntry.raw_entry`` dicts.

Pure functions — no I/O, no subscriber wiring. Given an iterable of raw JSONL
entries (exactly what the Claude Code CLI writes locally and what this
package's ``SessionStore`` adapter mirrors into ``ClaudeSDKEntry`` events),
yield ``(canonical_event, metadata)`` tuples that a caller can serialize to a
parallel stream.

Design decisions (DEV-1508):

- **Read-side**. This module operates on already-persisted entries and never
  touches the ``SessionStore`` adapter. The adapter's
  ``load(append) == entries`` invariant is unaffected — consumers who don't
  need cross-framework reads can ignore this module entirely.
- **Block-by-block**. One CLI entry can produce zero or more canonical events.
  An assistant entry with interleaved ``text`` / ``tool_use`` blocks yields one
  ``AssistantTextGenerated`` per text block and (if any ``tool_use`` blocks
  are present) one combined ``AssistantToolCallsGenerated`` with all calls —
  that matches how readers think about "a turn made these tool calls".
- **Thinking blocks have no canonical home.** They ride in
  ``extensions.claude_sdk.thinking`` on the first canonical event emitted for
  the entry. If the entry only contains thinking blocks, nothing canonical is
  emitted — the raw entry is still persisted on ``ClaudeSDKEntry`` so no data
  is lost, just not surfaced cross-framework.
- **CLI-internal entry types produce nothing.** ``attachment``, ``system``,
  ``permission-mode``, ``last-prompt``, ``file-history-snapshot``,
  ``queue-operation`` have no canonical analogue.
- **Usage is metadata, not payload.** Per ``SCHEMA.md §3.4``, token counts
  attach as KurrentDB event metadata under ``$usage``. Canonical field names
  mirror ``KurrentDBChatHistoryProvider`` (MAF .NET): ``input_tokens`` /
  ``output_tokens`` / ``total_tokens`` / ``cached_input_tokens`` /
  ``reasoning_tokens`` / optional ``model`` / optional ``additional_counts``.
  Anthropic's split of ``cache_read_input_tokens`` vs
  ``cache_creation_input_tokens`` collapses into ``cached_input_tokens`` for
  the read bucket; the rest of the Anthropic-specific usage stanza (cache
  breakdown, server_tool_use, iterations, etc.) lands in
  ``additional_counts`` so nothing is silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from ._schema import events as _events
from ._schema.events import CLAUDE_SDK_EXTENSION_KEY

# Entries the CLI writes that carry no conversational payload. Preserved
# verbatim on the ClaudeSDKEntry stream but not surfaced as canonical events.
_CLI_INTERNAL_TYPES: frozenset[str] = frozenset(
    {
        "attachment",
        "system",
        "permission-mode",
        "last-prompt",
        "file-history-snapshot",
        "queue-operation",
    }
)


DecomposedEvent = tuple[_events._EventBase, dict[str, Any] | None]
"""One canonical event plus optional KurrentDB event metadata (``$usage`` shim)."""


def decompose_entry(
    entry: dict[str, Any],
    *,
    message_index: int,
    now: datetime | None = None,
) -> list[DecomposedEvent]:
    """Decompose one raw JSONL entry into canonical events.

    ``message_index`` is the starting index for this entry's first emitted
    event. Subsequent events from the same entry receive
    ``message_index + 1``, ``+2``, etc. Callers using the stream form
    (:func:`decompose_stream`) don't need to manage this by hand.

    Returns ``[]`` for entries with no canonical projection (CLI-internal
    types, entries with no decomposable content blocks).
    """
    entry_type = entry.get("type")
    if not entry_type or entry_type in _CLI_INTERNAL_TYPES:
        return []

    now = now or datetime.now(UTC)

    if entry_type == "user":
        return _decompose_user(entry, message_index, now)
    if entry_type == "assistant":
        return _decompose_assistant(entry, message_index, now)
    return []


def decompose_stream(
    entries: Iterable[dict[str, Any]],
    *,
    start_index: int = 0,
    now: datetime | None = None,
) -> Iterator[DecomposedEvent]:
    """Stream form of :func:`decompose_entry`.

    Tracks ``message_index`` across entries so the caller doesn't have to.
    ``start_index`` lets resumable pipelines pick up where they left off.
    """
    idx = start_index
    for entry in entries:
        for event, meta in decompose_entry(entry, message_index=idx, now=now):
            yield event, meta
            idx += 1


# --- user entries ------------------------------------------------------------


def _decompose_user(
    entry: dict[str, Any], message_index: int, now: datetime
) -> list[DecomposedEvent]:
    message = entry.get("message") or {}
    content = message.get("content")
    entry_uuid = str(entry.get("uuid") or "")
    created_at = _parse_ts(entry.get("timestamp")) or now

    # Plain string prompt — the common case.
    if isinstance(content, str):
        return [
            (
                _events.UserMessageReceived(
                    content=content,
                    message_id=entry_uuid,
                    message_index=message_index,
                    created_at=created_at,
                    timestamp=now,
                ),
                None,
            )
        ]

    if not isinstance(content, list):
        return []

    # A user entry whose content is a list is one of:
    #   (a) a container for ``tool_result`` blocks (common after the assistant
    #       issued tool_use blocks in the prior entry), or
    #   (b) a multi-block text prompt (rare but allowed).
    # Tool results take precedence because they're the semantically meaningful
    # shape; a mixed list would be unusual and we'd still want to surface the
    # tool results as canonical events.
    tool_results = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    if tool_results:
        return [
            _tool_result_event(
                block, entry_uuid, message_index + offset, created_at, now
            )
            for offset, block in enumerate(tool_results)
        ]

    # Fall back to concatenated text — mirrors how most SDKs surface
    # multi-block user prompts as a single user message.
    text_parts = [
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    if any(text_parts):
        return [
            (
                _events.UserMessageReceived(
                    content="".join(text_parts),
                    message_id=entry_uuid,
                    message_index=message_index,
                    created_at=created_at,
                    timestamp=now,
                ),
                None,
            )
        ]

    return []


def _tool_result_event(
    block: dict[str, Any],
    entry_uuid: str,
    message_index: int,
    created_at: datetime,
    now: datetime,
) -> DecomposedEvent:
    is_error = block.get("is_error")
    extensions = (
        {CLAUDE_SDK_EXTENSION_KEY: {"is_error": bool(is_error)}}
        if is_error is not None
        else None
    )
    event = _events.ToolResultReceived(
        call_id=str(block.get("tool_use_id") or ""),
        result=_stringify_tool_result(block.get("content")),
        message_id=entry_uuid,
        message_index=message_index,
        created_at=created_at,
        timestamp=now,
        extensions=extensions,
    )
    return event, None


def _stringify_tool_result(content: Any) -> str | None:
    """Reduce a tool_result's ``content`` to a single string.

    Anthropic's tool_result content can be a string or a list of content
    blocks (``text``, ``image``, etc.). Canonical ``ToolResultReceived.result``
    is a single optional string; we flatten list-of-blocks to concatenated
    text, or JSON-encode as a fallback for non-textual blocks.
    """
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_pieces: list[str] = []
        has_non_text = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_pieces.append(str(block.get("text") or ""))
            else:
                has_non_text = True
        if text_pieces and not has_non_text:
            return "".join(text_pieces)
        return json.dumps(content, separators=(",", ":"))
    return json.dumps(content, separators=(",", ":"))


# --- assistant entries -------------------------------------------------------


def _decompose_assistant(
    entry: dict[str, Any], message_index: int, now: datetime
) -> list[DecomposedEvent]:
    message = entry.get("message") or {}
    content = message.get("content")
    entry_uuid = str(entry.get("uuid") or "")
    created_at = _parse_ts(entry.get("timestamp")) or now

    if not isinstance(content, list):
        return []

    text_blocks: list[dict[str, Any]] = []
    tool_use_blocks: list[dict[str, Any]] = []
    thinking_blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_blocks.append(block)
        elif block_type == "tool_use":
            tool_use_blocks.append(block)
        elif block_type == "thinking":
            thinking_blocks.append(block)

    usage_meta = _usage_metadata(message.get("usage"), message.get("model"))
    first_event_extensions = _assistant_extensions(message, thinking_blocks)

    results: list[DecomposedEvent] = []
    idx = message_index

    # Emit one AssistantTextGenerated per text block. Usage metadata and the
    # extensions envelope (thinking, stop_reason, anthropic_message_id) ride
    # on whatever canonical event is emitted first — losing neither.
    for block in text_blocks:
        extensions = first_event_extensions if not results else None
        metadata = usage_meta if not results else None
        results.append(
            (
                _events.AssistantTextGenerated(
                    content=str(block.get("text") or ""),
                    message_id=entry_uuid,
                    message_index=idx,
                    created_at=created_at,
                    timestamp=now,
                    extensions=extensions,
                ),
                metadata,
            )
        )
        idx += 1

    if tool_use_blocks:
        extensions = first_event_extensions if not results else None
        metadata = usage_meta if not results else None
        tool_calls = [
            _events.ToolCallInfo(
                call_id=str(block.get("id") or ""),
                tool_name=str(block.get("name") or ""),
                arguments=block.get("input")
                if isinstance(block.get("input"), dict)
                else {},
            )
            for block in tool_use_blocks
        ]
        results.append(
            (
                _events.AssistantToolCallsGenerated(
                    tool_calls=tool_calls,
                    message_id=entry_uuid,
                    message_index=idx,
                    created_at=created_at,
                    timestamp=now,
                    extensions=extensions,
                ),
                metadata,
            )
        )
        idx += 1

    # Pure-thinking assistant entries produce no canonical events. Usage
    # metadata (if any) is lost from the canonical projection — the raw entry
    # on ClaudeSDKEntry is still authoritative. Emitting an empty placeholder
    # would misrepresent the conversation to cross-framework readers.
    return results


def _assistant_extensions(
    message: dict[str, Any], thinking_blocks: list[dict[str, Any]]
) -> dict[str, Any] | None:
    ext: dict[str, Any] = {}
    if thinking_blocks:
        ext["thinking"] = [
            {k: v for k, v in block.items() if k != "type"}
            for block in thinking_blocks
        ]
    stop_reason = message.get("stop_reason")
    if stop_reason:
        ext["stop_reason"] = stop_reason
    anthropic_id = message.get("id")
    if anthropic_id:
        ext["anthropic_message_id"] = anthropic_id
    if not ext:
        return None
    return {CLAUDE_SDK_EXTENSION_KEY: ext}


# --- usage metadata ---------------------------------------------------------


_ANTHROPIC_USAGE_EXTRA_KEYS: tuple[str, ...] = (
    "cache_creation_input_tokens",
    "cache_creation",
    "server_tool_use",
    "service_tier",
    "inference_geo",
    "iterations",
    "speed",
)


def _usage_metadata(
    usage: Any, model: Any
) -> dict[str, Any] | None:
    """Translate an Anthropic assistant ``usage`` stanza into the ``$usage`` shim.

    Canonical field names mirror ``KurrentDBChatHistoryProvider`` (MAF .NET).
    Anthropic-specific fields that have no canonical slot land in
    ``additional_counts`` so nothing is silently dropped.
    """
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cache_read = usage.get("cache_read_input_tokens")

    total_tokens: int | None = None
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens

    additional = {
        key: usage[key] for key in _ANTHROPIC_USAGE_EXTRA_KEYS if key in usage
    }

    shim: dict[str, Any] = {}
    if input_tokens is not None:
        shim["input_tokens"] = input_tokens
    if output_tokens is not None:
        shim["output_tokens"] = output_tokens
    if total_tokens is not None:
        shim["total_tokens"] = total_tokens
    if cache_read is not None:
        shim["cached_input_tokens"] = cache_read
    if model:
        shim["model"] = str(model)
    if additional:
        shim["additional_counts"] = additional

    return {"$usage": shim} if shim else None


# --- helpers -----------------------------------------------------------------


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
