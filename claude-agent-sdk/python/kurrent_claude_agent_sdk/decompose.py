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
- **Block-by-block, in original order**. One CLI entry can produce zero or
  more canonical events. An assistant entry with interleaved ``text`` /
  ``tool_use`` blocks yields one ``AssistantTextGenerated`` per text block
  and (if any ``tool_use`` blocks are present) a single combined
  ``AssistantToolCallsGenerated`` carrying all calls. The grouped
  tool-calls event is emitted **at the position of the first** ``tool_use``
  block so text/tool relative ordering is preserved —
  ``[text, tool_use_A, tool_use_B, text]`` decomposes to
  ``AssistantTextGenerated, AssistantToolCallsGenerated, AssistantTextGenerated``,
  not to text-blocks-first-then-tool-calls.
- **Thinking blocks have no canonical home.** They ride in
  ``extensions.claude_sdk.thinking`` on the first canonical event emitted for
  the entry. If the entry only contains thinking blocks and no usage stanza,
  nothing canonical is emitted — the raw entry is still persisted on
  ``ClaudeSDKEntry``. A thinking-only entry that *does* carry ``$usage`` gets
  a single carrier ``AssistantTextGenerated(content=None)`` so the metadata
  and thinking extension survive the projection (SCHEMA.md §3.4 requires
  token usage to ride on an assistant event).
- **CLI-internal entry types produce nothing.** ``attachment``, ``system``,
  ``permission-mode``, ``last-prompt``, ``file-history-snapshot``,
  ``queue-operation`` have no canonical analogue.
- **Usage is metadata, not payload.** Per ``SCHEMA.md §3.4``, token counts
  attach as KurrentDB event metadata under ``$usage``. Canonical field names
  mirror ``KurrentDBChatHistoryProvider`` (MAF .NET): ``input_tokens`` /
  ``output_tokens`` / ``total_tokens`` / ``cached_input_tokens`` /
  ``reasoning_tokens`` / optional ``model`` / optional ``additional_counts``.
  Anthropic's ``cache_read_input_tokens`` is mapped to ``cached_input_tokens``;
  **every other key on the usage stanza** (cache_creation breakdown,
  server_tool_use, service_tier, iterations, future fields we haven't seen) is
  passed through verbatim under ``additional_counts``. This is a catch-all,
  not a whitelist — Anthropic additions survive without a decomposer change.
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
                block,
                entry_uuid,
                block_index=offset,
                message_index=message_index + offset,
                created_at=created_at,
                now=now,
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
    *,
    block_index: int,
    message_index: int,
    created_at: datetime,
    now: datetime,
) -> DecomposedEvent:
    tool_use_id = block.get("tool_use_id")
    call_id, missing_id = _resolve_call_id(
        raw=tool_use_id, entry_uuid=entry_uuid, prefix="tr", block_index=block_index
    )

    claude_sdk_ext: dict[str, Any] = {}
    is_error = block.get("is_error")
    if is_error is not None:
        claude_sdk_ext["is_error"] = bool(is_error)
    if missing_id:
        claude_sdk_ext["missing_tool_id"] = True
    extensions = (
        {CLAUDE_SDK_EXTENSION_KEY: claude_sdk_ext} if claude_sdk_ext else None
    )

    event = _events.ToolResultReceived(
        call_id=call_id,
        result=_stringify_tool_result(block.get("content")),
        message_id=entry_uuid,
        message_index=message_index,
        created_at=created_at,
        timestamp=now,
        extensions=extensions,
    )
    return event, None


def _tool_call_id(block: dict[str, Any], entry_uuid: str, block_index: int) -> str:
    """Return the tool-call id for a ``tool_use`` block, with deterministic
    fallback if the Anthropic API ever omits it.
    """
    call_id, _ = _resolve_call_id(
        raw=block.get("id"), entry_uuid=entry_uuid, prefix="tc", block_index=block_index
    )
    return call_id


def _resolve_call_id(
    *, raw: Any, entry_uuid: str, prefix: str, block_index: int
) -> tuple[str, bool]:
    """Return ``(call_id, missing_id)``.

    When the source block carries a non-empty id/tool_use_id, use it
    verbatim. When it's missing or empty, synthesise a deterministic
    fallback of the form ``{entry_uuid}:{prefix}{block_index}`` so two
    parallel calls in the same entry stay distinguishable and tool results
    can still be correlated positionally. Empty call_ids would collide on
    the schema's required join key.
    """
    if isinstance(raw, str) and raw:
        return raw, False
    return f"{entry_uuid}:{prefix}{block_index}", True


def _tool_call_arguments(raw_input: Any) -> dict[str, Any] | None:
    """Map a ``tool_use.input`` onto ``ToolCallInfo.arguments``.

    Anthropic's Messages API always emits dict inputs, but be defensive:
    preserve non-dict values under a single ``_raw`` key rather than
    silently coercing to ``{}`` and losing data. ``None`` means the block
    had no ``input`` at all.
    """
    if raw_input is None:
        return None
    if isinstance(raw_input, dict):
        return raw_input
    return {"_raw": raw_input}


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

    # Collect thinking + all tool_use blocks up front (both inform events we
    # emit later), but walk ``content`` in-order when actually generating
    # events so ``[text, tool_use, text]`` or ``[tool_use, text]`` keep their
    # original relative shape. The grouped tool-calls event is emitted once,
    # at the position of the **first** ``tool_use`` block encountered.
    thinking_blocks = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "thinking"
    ]
    tool_use_blocks = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]

    usage_meta = _usage_metadata(message.get("usage"), message.get("model"))
    first_event_extensions = _assistant_extensions(message, thinking_blocks)

    results: list[DecomposedEvent] = []
    idx = message_index
    tool_event_emitted = False

    def _next_extensions() -> dict[str, Any] | None:
        return first_event_extensions if not results else None

    def _next_metadata() -> dict[str, Any] | None:
        return usage_meta if not results else None

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            results.append(
                (
                    _events.AssistantTextGenerated(
                        content=str(block.get("text") or ""),
                        message_id=entry_uuid,
                        message_index=idx,
                        created_at=created_at,
                        timestamp=now,
                        extensions=_next_extensions(),
                    ),
                    _next_metadata(),
                )
            )
            idx += 1
        elif block_type == "tool_use" and not tool_event_emitted:
            tool_calls = [
                _events.ToolCallInfo(
                    call_id=_tool_call_id(b, entry_uuid, block_index),
                    tool_name=str(b.get("name") or ""),
                    arguments=_tool_call_arguments(b.get("input")),
                )
                for block_index, b in enumerate(tool_use_blocks)
            ]
            results.append(
                (
                    _events.AssistantToolCallsGenerated(
                        tool_calls=tool_calls,
                        message_id=entry_uuid,
                        message_index=idx,
                        created_at=created_at,
                        timestamp=now,
                        extensions=_next_extensions(),
                    ),
                    _next_metadata(),
                )
            )
            idx += 1
            tool_event_emitted = True
        # thinking blocks ride in extensions on the first emitted event;
        # later tool_use blocks are absorbed into the grouped tool-calls
        # event emitted at the first occurrence, so no event is produced
        # here for either.

    # Preserve ``$usage`` even when no text or tool_use blocks were emitted —
    # e.g. a pure-thinking assistant turn that still consumed tokens. Dropping
    # it would violate SCHEMA.md §3.4 which requires token usage to ride on
    # assistant events as ``$usage`` metadata. Content is None so the carrier
    # event doesn't misrepresent a non-existent text reply to cross-framework
    # readers; thinking / stop_reason extensions also come along. Thinking-only
    # turns without any usage keep producing nothing — the raw ClaudeSDKEntry
    # is authoritative there, with no schema rule at stake.
    if not results and usage_meta is not None:
        results.append(
            (
                _events.AssistantTextGenerated(
                    content=None,
                    message_id=entry_uuid,
                    message_index=idx,
                    created_at=created_at,
                    timestamp=now,
                    extensions=first_event_extensions,
                ),
                usage_meta,
            )
        )

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


# Keys we map into canonical ``$usage`` slots. Any other key on the Anthropic
# ``usage`` stanza is treated as forward-compatible overflow and lands verbatim
# in ``additional_counts`` — that's how we keep "nothing silently dropped"
# true even when Anthropic adds a field we've never seen before.
_MAPPED_USAGE_KEYS: frozenset[str] = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "reasoning_tokens",
    }
)


def _usage_metadata(
    usage: Any, model: Any
) -> dict[str, Any] | None:
    """Translate an Anthropic assistant ``usage`` stanza into the ``$usage`` shim.

    Canonical field names mirror ``KurrentDBChatHistoryProvider`` (MAF .NET).
    Everything outside the canonical slots is passed through verbatim under
    ``additional_counts`` (formalised in ``SCHEMA.md §3.4``) — a catch-all
    rather than a fixed whitelist, so a future Anthropic addition is
    automatically preserved instead of silently dropped.
    """
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cache_read = usage.get("cache_read_input_tokens")
    reasoning_tokens = usage.get("reasoning_tokens")

    # Prefer a provider-emitted ``total_tokens`` when present; fall back to
    # computing input+output. Anthropic doesn't emit it today, but be
    # forward-compatible — a future API revision might.
    total_tokens = usage.get("total_tokens")
    if not isinstance(total_tokens, int) and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens

    additional = {k: v for k, v in usage.items() if k not in _MAPPED_USAGE_KEYS}

    shim: dict[str, Any] = {}
    if input_tokens is not None:
        shim["input_tokens"] = input_tokens
    if output_tokens is not None:
        shim["output_tokens"] = output_tokens
    if total_tokens is not None:
        shim["total_tokens"] = total_tokens
    if cache_read is not None:
        shim["cached_input_tokens"] = cache_read
    if isinstance(reasoning_tokens, int):
        shim["reasoning_tokens"] = reasoning_tokens
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
