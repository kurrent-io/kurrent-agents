"""Translator unit tests, driven by the schema fixture JSON files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kurrent_ag_ui.translator import TranslateContext, translate

FIXTURES = Path(__file__).resolve().parents[3] / "schema" / "fixtures" / "events"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _ctx() -> TranslateContext:
    return TranslateContext(thread_id="sess-test", run_id="sess-test")


# ---------------------------------------------------------------- lifecycle


def test_session_started_emits_run_started():
    out = translate("SessionStarted", _load("SessionStarted"), _ctx())
    assert out == [{"type": "RUN_STARTED", "threadId": "sess-test", "runId": "sess-test"}]


def test_session_ended_emits_run_finished_once():
    ctx = _ctx()
    payload = _load("SessionEnded")
    first = translate("SessionEnded", payload, ctx)
    assert first == [{"type": "RUN_FINISHED", "threadId": "sess-test", "runId": "sess-test"}]
    # Idempotent: a second SessionEnded does not double-emit RUN_FINISHED.
    assert translate("SessionEnded", payload, ctx) == []


# ---------------------------------------------------------------- conversation


def test_user_message_round_trip():
    out = translate("UserMessageReceived", _load("UserMessageReceived"), _ctx())
    types = [e["type"] for e in out]
    assert types == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert out[0]["role"] == "user"
    assert out[0]["messageId"] == "msg-001"
    assert out[1]["delta"] == "What's the weather in Oslo today?"
    assert out[2]["messageId"] == "msg-001"


def test_assistant_text_sets_last_message_id():
    ctx = _ctx()
    out = translate("AssistantTextGenerated", _load("AssistantTextGenerated"), ctx)
    assert ctx.last_assistant_message_id == "msg-002"
    assert [e["type"] for e in out] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert out[0]["role"] == "assistant"


def test_assistant_thinking_plaintext():
    out = translate("AssistantThinkingGenerated", _load("AssistantThinkingGenerated"), _ctx())
    types = [e["type"] for e in out]
    assert types == ["REASONING_MESSAGE_START", "REASONING_MESSAGE_CONTENT", "REASONING_MESSAGE_END"]


def test_assistant_thinking_encrypted_no_content_skipped():
    out = translate(
        "AssistantThinkingGenerated",
        {"encrypted": True, "message_id": "rsn-1", "timestamp": "2026-01-01T00:00:00Z"},
        _ctx(),
    )
    assert out == []


def test_tool_calls_emit_carrier_text_then_tool_call_triple():
    payload = _load("AssistantToolCallsGenerated")
    ctx = _ctx()
    out = translate("AssistantToolCallsGenerated", payload, ctx)
    types = [e["type"] for e in out]
    # carrier text ("Let me look that up.") + 1 tool call x 3 events
    assert types == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    start = out[3]
    assert start["toolCallId"] == "call-001"
    assert start["toolCallName"] == "search"
    assert start["parentMessageId"] == "msg-003"
    args = json.loads(out[4]["delta"])
    assert args == {"query": "weather Oslo"}
    assert out[5]["toolCallId"] == "call-001"


def test_tool_calls_no_carrier_text():
    payload = {
        "tool_calls": [{"call_id": "c1", "tool_name": "noop", "arguments": {}}],
        "message_id": "asst-x",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    out = translate("AssistantToolCallsGenerated", payload, _ctx())
    assert [e["type"] for e in out] == ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"]
    assert out[1]["delta"] == "{}"


def test_tool_result_round_trip():
    out = translate("ToolResultReceived", _load("ToolResultReceived"), _ctx())
    assert len(out) == 1
    e = out[0]
    assert e["type"] == "TOOL_CALL_RESULT"
    assert e["toolCallId"] == "call-001"
    # canonical result is already a string; pass through verbatim
    assert json.loads(e["content"]) == {"temperature_c": 8, "condition": "light_rain"}
    assert e["role"] == "tool"


# ---------------------------------------------------------------- custom-passthrough


def test_unknown_event_emits_custom():
    out = translate("UnknownThing", {"foo": "bar"}, _ctx())
    assert out == [{"type": "CUSTOM", "name": "canonical.UnknownThing", "value": {"foo": "bar"}}]


def test_unknown_event_dropped_when_passthrough_disabled():
    ctx = _ctx()
    ctx.custom_passthrough = False
    assert translate("UnknownThing", {}, ctx) == []


@pytest.mark.parametrize(
    "name,custom_name",
    [
        ("InterruptIssued", "interrupt.issued"),
        ("InterruptResolved", "interrupt.resolved"),
        ("SubagentStarted", "subagent.started"),
        ("SubagentCompleted", "subagent.completed"),
    ],
)
def test_custom_named_events(name: str, custom_name: str):
    out = translate(name, _load(name), _ctx())
    assert len(out) == 1
    assert out[0]["type"] == "CUSTOM"
    assert out[0]["name"] == custom_name


# ---------------------------------------------------------------- end-to-end story


def test_full_two_turn_session():
    """The thesis from demo/README.md: same canonical events produce a sane
    AG-UI event sequence regardless of source framework."""
    ctx = _ctx()
    sequence: list[tuple[str, dict]] = [
        ("SessionStarted", _load("SessionStarted")),
        ("UserMessageReceived", _load("UserMessageReceived")),
        ("AssistantToolCallsGenerated", _load("AssistantToolCallsGenerated")),
        ("ToolResultReceived", _load("ToolResultReceived")),
        ("AssistantTextGenerated", _load("AssistantTextGenerated")),
        ("SessionEnded", _load("SessionEnded")),
    ]
    out: list[dict] = []
    for et, p in sequence:
        out.extend(translate(et, p, ctx))
    types = [e["type"] for e in out]
    assert types == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        # tool-calls carrier text:
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
