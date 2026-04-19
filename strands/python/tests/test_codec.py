"""Unit tests for the Strands Message ↔ canonical codec."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kurrent_strands._codec import (
    canonical_to_messages,
    extract_usage_metadata,
    message_to_canonical,
)
from kurrent_strands._schema.events import (
    STRANDS_EXTENSION_KEY,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    ToolResultReceived,
    UserMessageReceived,
)


TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _msg(role: str, content: list[dict[str, Any]], **metadata: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": role, "content": content}
    if metadata:
        msg["metadata"] = metadata
    return msg


class TestUserMessage:
    def test_plain_text(self) -> None:
        events = message_to_canonical(
            _msg("user", [{"text": "hello"}]), message_index=0, timestamp=TS
        )
        assert len(events) == 1
        assert isinstance(events[0], UserMessageReceived)
        assert events[0].content == "hello"
        assert events[0].message_index == 0

    def test_round_trip(self) -> None:
        original = _msg("user", [{"text": "hello"}])
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["role"] == "user"
        assert restored["content"] == [{"text": "hello"}]


class TestAssistantMessage:
    def test_plain_text(self) -> None:
        events = message_to_canonical(
            _msg("assistant", [{"text": "hi there"}]),
            message_index=1,
            timestamp=TS,
        )
        assert len(events) == 1
        assert isinstance(events[0], AssistantTextGenerated)

    def test_tool_use_with_text(self) -> None:
        events = message_to_canonical(
            _msg(
                "assistant",
                [
                    {"text": "Let me search."},
                    {
                        "toolUse": {
                            "toolUseId": "c1",
                            "name": "search",
                            "input": {"q": "kurrent"},
                        }
                    },
                ],
            ),
            message_index=2,
            timestamp=TS,
        )
        assert len(events) == 1
        assert isinstance(events[0], AssistantToolCallsGenerated)
        assert events[0].content == "Let me search."
        assert events[0].tool_calls[0].call_id == "c1"
        assert events[0].tool_calls[0].tool_name == "search"
        assert events[0].tool_calls[0].arguments == {"q": "kurrent"}

    def test_tool_call_round_trip(self) -> None:
        original = _msg(
            "assistant",
            [
                {"text": "Let me search."},
                {
                    "toolUse": {
                        "toolUseId": "c1",
                        "name": "search",
                        "input": {"q": "x"},
                    }
                },
            ],
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["role"] == "assistant"
        # Text block, then toolUse block.
        assert restored["content"][0] == {"text": "Let me search."}
        assert restored["content"][1]["toolUse"]["toolUseId"] == "c1"
        assert restored["content"][1]["toolUse"]["name"] == "search"
        assert restored["content"][1]["toolUse"]["input"] == {"q": "x"}


class TestToolResult:
    def test_tool_result_on_user_role(self) -> None:
        events = message_to_canonical(
            _msg(
                "user",
                [
                    {
                        "toolResult": {
                            "toolUseId": "c1",
                            "content": [{"text": "found 3 hits"}],
                        }
                    }
                ],
            ),
            message_index=3,
            timestamp=TS,
        )
        assert len(events) == 1
        assert isinstance(events[0], ToolResultReceived)
        assert events[0].call_id == "c1"

    def test_status_round_trips_via_extensions(self) -> None:
        """Strands' ``ToolResult.status`` is required by the Anthropic adapter but
        isn't in the canonical schema — must ride in ``extensions.strands``
        and restore on the reconstructed message.
        """
        original = _msg(
            "user",
            [
                {
                    "toolResult": {
                        "toolUseId": "c1",
                        "content": [{"text": "boom"}],
                        "status": "error",
                    }
                }
            ],
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["content"][0]["toolResult"]["status"] == "error"

    def test_tool_result_default_status_is_success(self) -> None:
        """If a reader encounters a ToolResultReceived with no strands extension
        (e.g. written by an ADK agent), status defaults to ``success`` so
        Strands' Anthropic adapter doesn't raise ``KeyError``.
        """
        from kurrent_strands._schema.events import ToolResultReceived

        events = [
            ToolResultReceived(
                call_id="c1",
                tool_name=None,
                result='[{"text":"ok"}]',
                message_index=0,
                timestamp=TS,
                # No extensions — simulates a cross-framework read.
                extensions=None,
            )
        ]
        [restored] = canonical_to_messages(events)
        assert restored["content"][0]["toolResult"]["status"] == "success"

    def test_tool_result_round_trip(self) -> None:
        original = _msg(
            "user",
            [
                {
                    "toolResult": {
                        "toolUseId": "c1",
                        "content": [{"text": "hit 1"}, {"json": {"x": 1}}],
                    }
                }
            ],
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["role"] == "user"
        assert restored["content"][0]["toolResult"]["toolUseId"] == "c1"
        assert restored["content"][0]["toolResult"]["content"] == [
            {"text": "hit 1"},
            {"json": {"x": 1}},
        ]


class TestNonCanonicalBlocks:
    def test_image_block_preserved_via_extensions(self) -> None:
        """Image / document / etc. blocks ride in extensions.strands and restore verbatim."""
        image_block = {
            "image": {"format": "png", "source": {"bytes": b"fake-png"}},
        }
        original = _msg(
            "user", [{"text": "look at this"}, image_block]
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        assert len(events) == 1
        strands_ext = events[0].extensions[STRANDS_EXTENSION_KEY]
        assert strands_ext["non_canonical_blocks"] == [image_block]

        [restored] = canonical_to_messages(events)
        # Text block first (from canonical), then the image (from extensions).
        assert restored["content"][0] == {"text": "look at this"}
        assert restored["content"][1] == image_block


class TestCustomMetadata:
    def test_custom_metadata_round_trips(self) -> None:
        original = _msg(
            "assistant",
            [{"text": "ok"}],
            custom={"source": "test-harness", "turn": 5},
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["metadata"]["custom"] == {
            "source": "test-harness",
            "turn": 5,
        }


class TestUsageExtraction:
    def test_returns_none_when_absent(self) -> None:
        msg = _msg("assistant", [{"text": "ok"}])
        assert extract_usage_metadata(msg) is None

    def test_maps_camelcase_to_snake_case(self) -> None:
        msg = _msg(
            "assistant",
            [{"text": "ok"}],
            usage={
                "inputTokens": 120,
                "outputTokens": 30,
                "totalTokens": 150,
                "cacheReadInputTokens": 0,
            },
        )
        usage = extract_usage_metadata(msg)
        assert usage == {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "cached_input_tokens": 0,
        }
