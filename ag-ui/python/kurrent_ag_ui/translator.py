"""Pure canonical → AG-UI translator.

The translator is a stateless function over (event_type, payload, ctx) where
``payload`` is the canonical event already parsed as a JSON dict (snake_case
keys, per ``schema/SCHEMA_v2.md``). It returns a list of AG-UI event dicts
(SCREAMING_SNAKE ``type`` values, camelCase fields, per
https://docs.ag-ui.com/sdk/js/core/events).

Mapping is documented in this package's README.

Why dict-in / dict-out: lets us unit-test directly against the JSON fixtures
in ``schema/fixtures/events/`` without depending on the protobuf runtime,
and lets the bridge layer worry about wire format separately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranslateContext:
    """Per-session translator state.

    Carries the AG-UI ``threadId`` / ``runId`` (both = canonical session_id
    by convention) and the most recent assistant ``messageId``, used as
    ``parentMessageId`` when emitting tool calls.
    """

    thread_id: str
    run_id: str
    last_assistant_message_id: str | None = None
    finished: bool = False
    custom_passthrough: bool = True
    """Whether unknown event types emit CUSTOM events. False = drop."""

    extra: dict[str, Any] = field(default_factory=dict)


def translate(
    event_type: str,
    payload: dict[str, Any],
    ctx: TranslateContext,
) -> list[dict[str, Any]]:
    """Translate one canonical event into zero or more AG-UI events.

    Returns AG-UI events as plain dicts; the caller serialises them.
    Mutates ``ctx`` to track the running message id for parent linking.
    """
    handler = _HANDLERS.get(event_type)
    if handler is not None:
        return handler(payload, ctx)
    if ctx.custom_passthrough:
        return [_custom(f"canonical.{event_type}", payload)]
    return []


# ---------------------------------------------------------------- handlers


def _on_session_started(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    return [
        {
            "type": "RUN_STARTED",
            "threadId": ctx.thread_id,
            "runId": ctx.run_id,
        }
    ]


def _on_session_ended(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    if ctx.finished:
        return []
    ctx.finished = True
    return [
        {
            "type": "RUN_FINISHED",
            "threadId": ctx.thread_id,
            "runId": ctx.run_id,
        }
    ]


def _on_user_message(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    message_id = payload.get("message_id") or _synth_id("usr", payload)
    content = payload.get("content", "")
    return _text_message(message_id, "user", content)


def _on_assistant_text(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    message_id = payload.get("message_id") or _synth_id("asst", payload)
    ctx.last_assistant_message_id = message_id
    content = payload.get("content", "")
    return _text_message(message_id, "assistant", content)


def _on_assistant_thinking(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    # Encrypted thinking with no plaintext: skip (no useful AG-UI rendering).
    if payload.get("encrypted") and not payload.get("content"):
        return []
    message_id = payload.get("message_id") or _synth_id("rsn", payload)
    content = payload.get("content", "")
    out: list[dict[str, Any]] = [
        {"type": "REASONING_MESSAGE_START", "messageId": message_id},
    ]
    if content:
        out.append(
            {
                "type": "REASONING_MESSAGE_CONTENT",
                "messageId": message_id,
                "delta": content,
            }
        )
    out.append({"type": "REASONING_MESSAGE_END", "messageId": message_id})
    return out


def _on_assistant_tool_calls(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    message_id = payload.get("message_id") or _synth_id("asst", payload)
    out: list[dict[str, Any]] = []
    # Optional carrier text on the same message.
    text = payload.get("content")
    if text:
        out.extend(_text_message(message_id, "assistant", text))
    ctx.last_assistant_message_id = message_id
    for call in payload.get("tool_calls", []) or []:
        call_id = call.get("call_id") or _synth_id("call", call)
        tool_name = call.get("tool_name", "")
        args = call.get("arguments", {}) or {}
        # AG-UI streams arguments as a string delta. We have the whole dict —
        # one ARGS event with the full JSON-encoded payload.
        args_delta = json.dumps(args, separators=(",", ":"), sort_keys=True)
        out.append(
            {
                "type": "TOOL_CALL_START",
                "toolCallId": call_id,
                "toolCallName": tool_name,
                "parentMessageId": message_id,
            }
        )
        out.append(
            {
                "type": "TOOL_CALL_ARGS",
                "toolCallId": call_id,
                "delta": args_delta,
            }
        )
        out.append({"type": "TOOL_CALL_END", "toolCallId": call_id})
    return out


def _on_tool_result(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    call_id = payload.get("call_id", "")
    result = payload.get("result", "")
    # AG-UI TOOL_CALL_RESULT requires a string ``content``. Canonical
    # ``result`` is already a string per SCHEMA_v2 §3 (frameworks JSON-encode
    # structured returns before writing).
    content = result if isinstance(result, str) else json.dumps(result, separators=(",", ":"))
    msg_id = payload.get("message_id") or _synth_id("tres", payload)
    return [
        {
            "type": "TOOL_CALL_RESULT",
            "messageId": msg_id,
            "toolCallId": call_id,
            "content": content,
            "role": "tool",
        }
    ]


def _on_interrupt_issued(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    return [_custom("interrupt.issued", payload)]


def _on_interrupt_resolved(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    return [_custom("interrupt.resolved", payload)]


def _on_subagent_started(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    return [_custom("subagent.started", payload)]


def _on_subagent_completed(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    return [_custom("subagent.completed", payload)]


def _on_session_continued_as(payload: dict[str, Any], ctx: TranslateContext) -> list[dict[str, Any]]:
    # Continuation is structural metadata, not turn content. Surface it via
    # CUSTOM so a reader UI can offer "follow into next session" affordance.
    return [_custom("session.continued_as", payload)]


# ---------------------------------------------------------------- helpers


def _text_message(message_id: str, role: str, content: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {
            "type": "TEXT_MESSAGE_START",
            "messageId": message_id,
            "role": role,
        }
    ]
    if content:
        out.append(
            {
                "type": "TEXT_MESSAGE_CONTENT",
                "messageId": message_id,
                "delta": content,
            }
        )
    out.append({"type": "TEXT_MESSAGE_END", "messageId": message_id})
    return out


def _custom(name: str, value: Any) -> dict[str, Any]:
    return {"type": "CUSTOM", "name": name, "value": value}


def _synth_id(prefix: str, payload: dict[str, Any]) -> str:
    # Stable enough for replay: use timestamp + index when present, else hash.
    ts = payload.get("timestamp") or payload.get("created_at") or ""
    idx = payload.get("message_index")
    if ts:
        return f"{prefix}-{ts}-{idx if idx is not None else ''}"
    return f"{prefix}-{abs(hash(json.dumps(payload, sort_keys=True, default=str)))}"


_HANDLERS: dict[str, Any] = {
    "SessionStarted": _on_session_started,
    "SessionEnded": _on_session_ended,
    "SessionContinuedAs": _on_session_continued_as,
    "UserMessageReceived": _on_user_message,
    "AssistantTextGenerated": _on_assistant_text,
    "AssistantThinkingGenerated": _on_assistant_thinking,
    "AssistantToolCallsGenerated": _on_assistant_tool_calls,
    "ToolResultReceived": _on_tool_result,
    "InterruptIssued": _on_interrupt_issued,
    "InterruptResolved": _on_interrupt_resolved,
    "SubagentStarted": _on_subagent_started,
    "SubagentCompleted": _on_subagent_completed,
}
