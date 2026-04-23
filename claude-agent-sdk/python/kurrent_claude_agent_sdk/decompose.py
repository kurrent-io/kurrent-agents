"""Canonical decomposition of ``ClaudeSDKEntry.raw_entry`` dicts.

Pure functions — no I/O, no subscriber wiring. Given an iterable of raw JSONL
entries (exactly what the Claude Code CLI writes locally and what this
package's ``SessionStore`` adapter mirrors into ``ClaudeSDKEntry`` events),
yield ``(canonical_event, metadata)`` tuples that a caller can serialize to a
parallel stream.

Design decisions (DEV-1508, DEV-1531):

- **Read-side**. This module operates on already-persisted entries and never
  touches the ``SessionStore`` adapter. The adapter's
  ``load(append) == entries`` invariant is unaffected — consumers who don't
  need cross-framework reads can ignore this module entirely.
- **Block-by-block, in original order**. One CLI entry can produce zero or
  more canonical events. An assistant entry with interleaved ``text`` /
  ``thinking`` / ``tool_use`` blocks yields one ``AssistantTextGenerated`` per
  text block, one ``AssistantThinkingGenerated`` per thinking block, and (if
  any ``tool_use`` blocks are present) a single combined
  ``AssistantToolCallsGenerated`` carrying all calls. The grouped
  tool-calls event is emitted **at the position of the first** ``tool_use``
  block so text/thinking/tool relative ordering is preserved.
- **Thinking becomes canonical (schema v2).** Claude Code emits plaintext
  thinking, so each thinking block produces one
  ``AssistantThinkingGenerated(content=..., encrypted=False)`` event. Any
  extra keys on the block (future provider fields) ride under
  ``extensions.claude_sdk.thinking_extras``. See SCHEMA_v2 §3.2 / §6.1.
- **CLI-internal entry types produce nothing.** ``attachment``, ``system``,
  ``permission-mode``, ``last-prompt``, ``file-history-snapshot``,
  ``queue-operation`` have no canonical analogue.
- **Usage is metadata, not payload.** Per SCHEMA_v2 §3.6, token counts attach
  as KurrentDB event metadata under :data:`USAGE_METADATA_KEY`. Canonical
  field names mirror :class:`TokenUsage`. Anthropic's
  ``cache_read_input_tokens`` maps to ``cached_input_tokens``; **every other
  key** on the usage stanza rides verbatim under ``additional_counts`` — a
  catch-all rather than a whitelist, so Anthropic additions survive without a
  decomposer change.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from kurrent_agent_schema import (
    USAGE_METADATA_KEY,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)
from pydantic import BaseModel

from .events import CLAUDE_SDK_EXTENSION_KEY

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


DecomposedEvent = tuple[BaseModel, dict[str, Any] | None]
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
                UserMessageReceived(
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
                UserMessageReceived(
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

    event = ToolResultReceived(
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


# Keys on a ``thinking`` block that map onto canonical
# ``AssistantThinkingGenerated`` fields; anything else rides in extensions.
_CANONICAL_THINKING_BLOCK_KEYS: frozenset[str] = frozenset({"type", "thinking", "signature"})


def _decompose_assistant(
    entry: dict[str, Any], message_index: int, now: datetime
) -> list[DecomposedEvent]:
    message = entry.get("message") or {}
    content = message.get("content")
    entry_uuid = str(entry.get("uuid") or "")
    created_at = _parse_ts(entry.get("timestamp")) or now

    if not isinstance(content, list):
        return []

    tool_use_blocks = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]

    usage_meta = _usage_metadata(message.get("usage"), message.get("model"))
    first_event_extensions = _assistant_extensions(message)

    results: list[DecomposedEvent] = []
    idx = message_index
    tool_event_emitted = False

    def _next_extensions(extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if results:
            return _with_extension(None, extra)
        return _with_extension(first_event_extensions, extra)

    def _next_metadata() -> dict[str, Any] | None:
        return usage_meta if not results else None

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            results.append(
                (
                    AssistantTextGenerated(
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
        elif block_type == "thinking":
            extras = _thinking_block_extras(block)
            thinking_ext = (
                {CLAUDE_SDK_EXTENSION_KEY: {"thinking_extras": extras}}
                if extras
                else None
            )
            results.append(
                (
                    AssistantThinkingGenerated(
                        content=str(block.get("thinking") or "") or None,
                        encrypted=False,  # Claude Code thinking is plaintext.
                        signature=_optional_str(block.get("signature")),
                        message_id=entry_uuid,
                        message_index=idx,
                        created_at=created_at,
                        timestamp=now,
                        extensions=_next_extensions(thinking_ext),
                    ),
                    _next_metadata(),
                )
            )
            idx += 1
        elif block_type == "tool_use" and not tool_event_emitted:
            tool_calls = [
                ToolCallInfo(
                    call_id=_tool_call_id(b, entry_uuid, block_index),
                    tool_name=str(b.get("name") or ""),
                    arguments=_tool_call_arguments(b.get("input")),
                )
                for block_index, b in enumerate(tool_use_blocks)
            ]
            results.append(
                (
                    AssistantToolCallsGenerated(
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
        # Later tool_use blocks are absorbed into the grouped tool-calls event
        # emitted at the first occurrence — no further event is produced here.

    # Preserve ``$usage`` even when no content blocks were emitted — e.g. an
    # assistant turn whose content we couldn't project but that still consumed
    # tokens. SCHEMA_v2 §3.6 requires token usage to ride on assistant events,
    # so drop a content-less ``AssistantTextGenerated`` carrier rather than
    # losing the metadata. This path is rare post-v2 (thinking is now its own
    # event type) but stays as a safety net for unexpected content shapes.
    if not results and usage_meta is not None:
        results.append(
            (
                AssistantTextGenerated(
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


def _assistant_extensions(message: dict[str, Any]) -> dict[str, Any] | None:
    ext: dict[str, Any] = {}
    stop_reason = message.get("stop_reason")
    if stop_reason:
        ext["stop_reason"] = stop_reason
    anthropic_id = message.get("id")
    if anthropic_id:
        ext["anthropic_message_id"] = anthropic_id
    if not ext:
        return None
    return {CLAUDE_SDK_EXTENSION_KEY: ext}


def _thinking_block_extras(block: dict[str, Any]) -> dict[str, Any]:
    """Return non-canonical keys on a thinking block, for forward-compat."""
    return {k: v for k, v in block.items() if k not in _CANONICAL_THINKING_BLOCK_KEYS}


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _with_extension(
    base: dict[str, Any] | None, extra: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Merge an extra extensions slice into ``base`` (which may itself be ``None``)."""
    if not extra:
        return base
    merged: dict[str, Any] = {k: dict(v) for k, v in base.items()} if base else {}
    for slug, payload in extra.items():
        existing = merged.get(slug)
        if isinstance(existing, dict) and isinstance(payload, dict):
            merged[slug] = {**existing, **payload}
        else:
            merged[slug] = payload
    return merged


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


def _usage_metadata(usage: Any, model: Any) -> dict[str, Any] | None:
    """Translate an Anthropic assistant ``usage`` stanza into the ``$usage`` shim.

    Canonical field names mirror :class:`kurrent_agent_schema.TokenUsage`.
    Everything outside the canonical slots is passed through verbatim under
    ``additional_counts`` (formalised in SCHEMA_v2 §3.6) — a catch-all rather
    than a fixed whitelist, so a future Anthropic addition is automatically
    preserved instead of silently dropped.
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

    return {USAGE_METADATA_KEY: shim} if shim else None


# --- helpers -----------------------------------------------------------------


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
