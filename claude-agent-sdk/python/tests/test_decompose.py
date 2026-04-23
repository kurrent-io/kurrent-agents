"""Unit tests for the read-side canonical decomposer.

Pure-function tests — no KurrentDB fixtures. Drive the decomposer with
synthetic JSONL dicts (the shape the Claude Code CLI writes to its local
transcript, which our ``SessionStore`` adapter mirrors as
``ClaudeSDKEntry.raw_entry``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from kurrent_claude_agent_sdk import decompose_entry, decompose_stream
from kurrent_claude_agent_sdk._schema import events as _events

NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)


def _user_string_entry(text: str, *, uuid: str = "u1") -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-04-23T06:46:20.999Z",
        "message": {"role": "user", "content": text},
    }


def _user_tool_results_entry(
    results: list[dict[str, Any]], *, uuid: str = "u-tr"
) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-04-23T06:47:00.000Z",
        "message": {"role": "user", "content": results},
    }


def _assistant_entry(
    blocks: list[dict[str, Any]],
    *,
    uuid: str = "a1",
    usage: dict[str, Any] | None = None,
    model: str = "claude-opus-4-7",
    stop_reason: str = "end_turn",
    message_id: str = "msg_001",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": message_id,
        "model": model,
        "role": "assistant",
        "content": blocks,
        "stop_reason": stop_reason,
    }
    if usage is not None:
        message["usage"] = usage
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": "2026-04-23T06:46:24.596Z",
        "message": message,
    }


class TestUserEntries:
    def test_plain_string_prompt_becomes_user_message_received(self) -> None:
        entry = _user_string_entry("Hi, what's 2+2?", uuid="u1")
        results = decompose_entry(entry, message_index=0, now=NOW)
        assert len(results) == 1
        event, metadata = results[0]
        assert isinstance(event, _events.UserMessageReceived)
        assert event.content == "Hi, what's 2+2?"
        assert event.message_id == "u1"
        assert event.message_index == 0
        assert metadata is None

    def test_tool_result_blocks_become_tool_result_received(self) -> None:
        entry = _user_tool_results_entry(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "result 1",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "content": "result 2",
                    "is_error": True,
                },
            ],
            uuid="u-tr",
        )
        results = decompose_entry(entry, message_index=5, now=NOW)
        assert len(results) == 2

        e0, _ = results[0]
        e1, _ = results[1]
        assert isinstance(e0, _events.ToolResultReceived)
        assert e0.call_id == "toolu_1"
        assert e0.result == "result 1"
        assert e0.message_index == 5
        assert e0.extensions is None

        assert isinstance(e1, _events.ToolResultReceived)
        assert e1.call_id == "toolu_2"
        assert e1.message_index == 6
        assert e1.extensions is not None
        assert e1.extensions["claude_sdk"]["is_error"] is True

    def test_tool_result_content_list_with_text_blocks_is_concatenated(
        self,
    ) -> None:
        entry = _user_tool_results_entry(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu",
                    "content": [
                        {"type": "text", "text": "line 1\n"},
                        {"type": "text", "text": "line 2"},
                    ],
                }
            ]
        )
        [(event, _)] = decompose_entry(entry, message_index=0, now=NOW)
        assert isinstance(event, _events.ToolResultReceived)
        assert event.result == "line 1\nline 2"

    def test_tool_result_content_with_non_text_blocks_is_json_encoded(
        self,
    ) -> None:
        entry = _user_tool_results_entry(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu",
                    "content": [
                        {"type": "text", "text": "see"},
                        {"type": "image", "source": {"data": "..."}},
                    ],
                }
            ]
        )
        [(event, _)] = decompose_entry(entry, message_index=0, now=NOW)
        assert isinstance(event, _events.ToolResultReceived)
        # Non-textual block forces JSON encoding so the reader doesn't silently
        # drop the image reference.
        assert event.result is not None
        assert "image" in event.result

    def test_multi_block_text_prompt_is_concatenated(self) -> None:
        entry = {
            "type": "user",
            "uuid": "u-multi",
            "timestamp": "2026-04-23T00:00:00Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part one. "},
                    {"type": "text", "text": "Part two."},
                ],
            },
        }
        [(event, _)] = decompose_entry(entry, message_index=0, now=NOW)
        assert isinstance(event, _events.UserMessageReceived)
        assert event.content == "Part one. Part two."


class TestAssistantEntries:
    def test_text_only_becomes_assistant_text_generated(self) -> None:
        entry = _assistant_entry(
            [{"type": "text", "text": "Hi Alexey! 2+2 = 4."}], uuid="a1"
        )
        results = decompose_entry(entry, message_index=1, now=NOW)
        assert len(results) == 1
        event, metadata = results[0]
        assert isinstance(event, _events.AssistantTextGenerated)
        assert event.content == "Hi Alexey! 2+2 = 4."
        assert event.message_id == "a1"
        assert event.message_index == 1
        # No usage stanza provided → no metadata.
        assert metadata is None
        # stop_reason + anthropic_message_id ride on first-event extensions.
        assert event.extensions is not None
        assert event.extensions["claude_sdk"]["stop_reason"] == "end_turn"
        assert event.extensions["claude_sdk"]["anthropic_message_id"] == "msg_001"

    def test_multiple_text_blocks_emit_one_event_each(self) -> None:
        entry = _assistant_entry(
            [
                {"type": "text", "text": "First. "},
                {"type": "text", "text": "Second."},
            ],
            uuid="a-multi",
        )
        results = decompose_entry(entry, message_index=0, now=NOW)
        assert [type(e).__name__ for e, _ in results] == [
            "AssistantTextGenerated",
            "AssistantTextGenerated",
        ]
        assert [e.content for e, _ in results] == ["First. ", "Second."]
        assert [e.message_index for e, _ in results] == [0, 1]

    def test_tool_use_blocks_group_into_one_event(self) -> None:
        entry = _assistant_entry(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search",
                    "input": {"query": "foo"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "fetch",
                    "input": {"url": "https://example.com"},
                },
            ],
            uuid="a-tc",
        )
        results = decompose_entry(entry, message_index=0, now=NOW)
        assert len(results) == 1
        event, _ = results[0]
        assert isinstance(event, _events.AssistantToolCallsGenerated)
        assert [tc.call_id for tc in event.tool_calls] == ["toolu_1", "toolu_2"]
        assert [tc.tool_name for tc in event.tool_calls] == ["search", "fetch"]
        assert event.tool_calls[0].arguments == {"query": "foo"}
        assert event.tool_calls[1].arguments == {"url": "https://example.com"}

    def test_mixed_text_and_tool_use_emits_both_in_order(self) -> None:
        entry = _assistant_entry(
            [
                {"type": "text", "text": "Looking it up..."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search",
                    "input": {"query": "x"},
                },
            ],
            uuid="a-mix",
        )
        results = decompose_entry(entry, message_index=0, now=NOW)
        assert [type(e).__name__ for e, _ in results] == [
            "AssistantTextGenerated",
            "AssistantToolCallsGenerated",
        ]
        assert [e.message_index for e, _ in results] == [0, 1]

    def test_thinking_block_rides_in_extensions_on_first_event(self) -> None:
        entry = _assistant_entry(
            [
                {"type": "thinking", "thinking": "let me compute 2+2"},
                {"type": "text", "text": "2+2 = 4"},
            ],
            uuid="a-think",
        )
        results = decompose_entry(entry, message_index=0, now=NOW)
        assert len(results) == 1
        event, _ = results[0]
        assert isinstance(event, _events.AssistantTextGenerated)
        thinking = event.extensions["claude_sdk"]["thinking"]
        assert thinking == [{"thinking": "let me compute 2+2"}]

    def test_pure_thinking_entry_emits_nothing(self) -> None:
        entry = _assistant_entry(
            [{"type": "thinking", "thinking": "silent reasoning"}],
            uuid="a-thonly",
        )
        assert decompose_entry(entry, message_index=0, now=NOW) == []


class TestUsageMetadata:
    def test_full_anthropic_usage_maps_to_shim(self) -> None:
        entry = _assistant_entry(
            [{"type": "text", "text": "hi"}],
            usage={
                "input_tokens": 6,
                "cache_creation_input_tokens": 40136,
                "cache_read_input_tokens": 12,
                "output_tokens": 21,
                "server_tool_use": {"web_search_requests": 0},
                "service_tier": "standard",
            },
            model="claude-opus-4-7",
        )
        [(event, metadata)] = decompose_entry(entry, message_index=0, now=NOW)
        assert metadata is not None
        shim = metadata["$usage"]
        assert shim["input_tokens"] == 6
        assert shim["output_tokens"] == 21
        assert shim["total_tokens"] == 27
        assert shim["cached_input_tokens"] == 12
        assert shim["model"] == "claude-opus-4-7"
        # Non-canonical fields land in additional_counts — not silently dropped.
        extras = shim["additional_counts"]
        assert extras["cache_creation_input_tokens"] == 40136
        assert extras["service_tier"] == "standard"
        assert extras["server_tool_use"] == {"web_search_requests": 0}
        # Sanity: first event also carries the extensions envelope.
        assert isinstance(event, _events.AssistantTextGenerated)

    def test_missing_usage_returns_no_metadata(self) -> None:
        entry = _assistant_entry([{"type": "text", "text": "hi"}], usage=None)
        [(_, metadata)] = decompose_entry(entry, message_index=0, now=NOW)
        assert metadata is None

    def test_usage_only_on_first_event_for_multi_event_entries(self) -> None:
        entry = _assistant_entry(
            [
                {"type": "text", "text": "First."},
                {"type": "text", "text": "Second."},
            ],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        results = decompose_entry(entry, message_index=0, now=NOW)
        metas = [meta for _, meta in results]
        assert metas[0] is not None
        assert metas[0]["$usage"]["total_tokens"] == 15
        assert metas[1] is None


class TestCLIInternalEntries:
    @pytest.mark.parametrize(
        "entry_type",
        [
            "attachment",
            "system",
            "permission-mode",
            "last-prompt",
            "file-history-snapshot",
            "queue-operation",
        ],
    )
    def test_cli_internal_entries_produce_nothing(self, entry_type: str) -> None:
        entry = {
            "type": entry_type,
            "uuid": "x",
            "timestamp": "2026-04-23T00:00:00Z",
            "foo": "bar",
        }
        assert decompose_entry(entry, message_index=0, now=NOW) == []

    def test_unknown_entry_type_produces_nothing(self) -> None:
        entry = {"type": "something-new", "uuid": "x"}
        assert decompose_entry(entry, message_index=0, now=NOW) == []

    def test_entry_without_type_produces_nothing(self) -> None:
        assert decompose_entry({"uuid": "x"}, message_index=0, now=NOW) == []


class TestStreamForm:
    def test_decompose_stream_assigns_monotonic_message_index(self) -> None:
        entries = [
            _user_string_entry("hello", uuid="u1"),
            _assistant_entry(
                [{"type": "text", "text": "hi there"}],
                uuid="a1",
                usage={"input_tokens": 5, "output_tokens": 2},
            ),
            _user_string_entry("thanks", uuid="u2"),
        ]
        results = list(decompose_stream(entries, now=NOW))
        assert [e.message_index for e, _ in results] == [0, 1, 2]
        # Usage metadata rides with the assistant event (index 1), not the others.
        assert results[0][1] is None
        assert results[1][1] is not None
        assert results[2][1] is None

    def test_decompose_stream_skips_cli_internal_entries_without_consuming_index(
        self,
    ) -> None:
        entries = [
            {"type": "attachment", "uuid": "att-1"},
            _user_string_entry("hi", uuid="u1"),
            {"type": "system", "uuid": "sys-1"},
            _assistant_entry(
                [{"type": "text", "text": "hey"}], uuid="a1"
            ),
        ]
        results = list(decompose_stream(entries, now=NOW))
        assert [e.message_index for e, _ in results] == [0, 1]

    def test_decompose_stream_respects_start_index(self) -> None:
        entries = [_user_string_entry("hi", uuid="u1")]
        [(event, _)] = list(decompose_stream(entries, start_index=42, now=NOW))
        assert event.message_index == 42


class TestLiveTranscriptShape:
    """Regression against the exact entry shape from a real CLI transcript.

    The concrete fixture here mirrors what ``samples/basic_agent/main.py``
    produced during DEV-1508 verification — ensures the decomposer stays
    aligned with what the shipped CLI actually writes.
    """

    def test_real_assistant_entry_decomposes_cleanly(self) -> None:
        entry = {
            "parentUuid": "f935694c-7407-40df-ba5b-28e5a77951a3",
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "id": "msg_01AC8tV5Bec6N9Ji5eQTZCZ3",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi Alexey! 2+2 = 4."}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 6,
                    "cache_creation_input_tokens": 40136,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 21,
                    "server_tool_use": {
                        "web_search_requests": 0,
                        "web_fetch_requests": 0,
                    },
                    "service_tier": "standard",
                },
            },
            "type": "assistant",
            "uuid": "8d1bec38-e5d5-44ef-83c9-27637970986d",
            "timestamp": "2026-04-23T06:46:24.596Z",
            "sessionId": "acb5a530-870b-4741-9eee-1d8036b7b96b",
        }
        [(event, metadata)] = decompose_entry(entry, message_index=0, now=NOW)
        assert isinstance(event, _events.AssistantTextGenerated)
        assert event.content == "Hi Alexey! 2+2 = 4."
        assert event.message_id == "8d1bec38-e5d5-44ef-83c9-27637970986d"
        assert metadata is not None
        shim = metadata["$usage"]
        assert shim["input_tokens"] == 6
        assert shim["output_tokens"] == 21
        assert shim["total_tokens"] == 27
        assert shim["cached_input_tokens"] == 0
        assert shim["model"] == "claude-opus-4-7"
