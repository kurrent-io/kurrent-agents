"""Unit tests for the Strands Message ↔ canonical codec."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from google.protobuf.json_format import MessageToDict
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    ToolResultReceived,
    UserMessageReceived,
)

from kurrent_strands._codec import (
    STRANDS_EXTENSION_KEY,
    canonical_to_messages,
    extract_usage_metadata,
    message_to_canonical,
)

TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _msg(role: str, content: list[dict[str, Any]], **metadata: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": role, "content": content}
    if metadata:
        msg["metadata"] = metadata
    return msg


def _strands_extension(event: Any) -> dict[str, Any]:
    """Read the ``strands`` extension block as a plain dict, or ``{}`` if absent."""
    if STRANDS_EXTENSION_KEY not in event.extensions:
        return {}
    return MessageToDict(
        event.extensions[STRANDS_EXTENSION_KEY], preserving_proto_field_name=True
    )


def _struct_to_dict(struct: Any) -> dict[str, Any]:
    return MessageToDict(struct, preserving_proto_field_name=True)


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
        assert _struct_to_dict(events[0].tool_calls[0].arguments) == {"q": "kurrent"}

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


class TestEmptyContent:
    """An assistant event whose ``content`` is explicitly empty (`""`) must
    survive round-trip — distinct from ``content`` being unset entirely.
    Pre-migration Pydantic logic preserved this; the proto codec does the
    same via ``HasField`` (rather than truthiness)."""

    def test_explicit_empty_assistant_text_round_trips(self) -> None:
        evt = AssistantTextGenerated(
            content="",
            message_index=0,
            timestamp=TS,
        )
        [restored] = canonical_to_messages([evt])
        assert restored["role"] == "assistant"
        assert restored["content"] == [{"text": ""}]


class TestThinkingContent:
    """``reasoningContent`` blocks emit ``AssistantThinkingGenerated`` per
    ``SCHEMA_v2.md §3.2``. Strands reasoning is plaintext; signature and any
    redacted-content bytes ride in ``extensions.strands.thinking``."""

    def test_plaintext_reasoning_emits_thinking_event(self) -> None:
        events = message_to_canonical(
            _msg(
                "assistant",
                [
                    {
                        "reasoningContent": {
                            "reasoningText": {"text": "Let me think."}
                        }
                    },
                    {"text": "Here's the answer."},
                ],
            ),
            message_index=3,
            timestamp=TS,
        )
        # Thinking event comes BEFORE the text event for the same turn.
        assert len(events) == 2
        assert isinstance(events[0], AssistantThinkingGenerated)
        assert events[0].content == "Let me think."
        assert events[0].HasField("encrypted") is False  # default, plaintext
        assert isinstance(events[1], AssistantTextGenerated)
        assert events[1].content == "Here's the answer."

    def test_signature_is_set_on_canonical_field(self) -> None:
        """``signature`` is a canonical field on ``AssistantThinkingGenerated``
        per SCHEMA_v2 §3.2 — must be readable by cross-SDK readers without
        decoding the strands extension envelope."""
        events = message_to_canonical(
            _msg(
                "assistant",
                [
                    {
                        "reasoningContent": {
                            "reasoningText": {
                                "text": "thoughts",
                                "signature": "sig-abc",
                            }
                        }
                    }
                ],
            ),
            message_index=0,
            timestamp=TS,
        )
        assert events[0].HasField("signature")
        assert events[0].signature == "sig-abc"
        # Signature does NOT also appear under extensions.strands.thinking —
        # canonical placement is the single source of truth.
        assert "thinking" not in _strands_extension(events[0])

    def test_redacted_content_round_trips_via_extensions(self) -> None:
        events = message_to_canonical(
            _msg(
                "assistant",
                [{"reasoningContent": {"redactedContent": b"opaque-bytes"}}],
            ),
            message_index=0,
            timestamp=TS,
        )
        thinking = _strands_extension(events[0])["thinking"]
        # Bytes ride as base64 in the JSON-shaped Struct extension.
        assert thinking["redacted_content"] == "b3BhcXVlLWJ5dGVz"  # base64 of 'opaque-bytes'

    def test_thinking_round_trip_reconstructs_reasoning_block(self) -> None:
        original = _msg(
            "assistant",
            [
                {
                    "reasoningContent": {
                        "reasoningText": {
                            "text": "step by step",
                            "signature": "sig-1",
                        }
                    }
                },
                {"text": "result"},
            ],
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["role"] == "assistant"
        assert restored["content"][0] == {
            "reasoningContent": {
                "reasoningText": {"text": "step by step", "signature": "sig-1"}
            }
        }
        assert restored["content"][1] == {"text": "result"}

    def test_redacted_round_trip_recovers_bytes(self) -> None:
        original = _msg(
            "assistant",
            [{"reasoningContent": {"redactedContent": b"opaque-bytes"}}],
        )
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        [restored] = canonical_to_messages(events)
        assert restored["content"][0] == {
            "reasoningContent": {"redactedContent": b"opaque-bytes"}
        }

    def test_malformed_redacted_base64_does_not_break_restore(self) -> None:
        """Corrupt or partial ``redacted_content`` must not abort session
        restore (`canonical_to_messages` is invoked from
        `KurrentDBSessionManager.initialize`)."""
        from google.protobuf.json_format import ParseDict
        from google.protobuf.struct_pb2 import Struct

        from kurrent_strands._codec import STRANDS_EXTENSION_KEY

        evt = AssistantThinkingGenerated(
            content="thoughts",
            message_index=0,
            timestamp=TS,
        )
        struct = Struct()
        ParseDict({"thinking": {"redacted_content": "not-valid-b64!!!"}}, struct)
        evt.extensions[STRANDS_EXTENSION_KEY].CopyFrom(struct)

        # Restore must succeed; the redactedContent simply gets omitted.
        [restored] = canonical_to_messages([evt])
        assert restored["role"] == "assistant"
        rc = restored["content"][0]["reasoningContent"]
        assert rc == {"reasoningText": {"text": "thoughts"}}


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
        """Strands' ``ToolResult.status`` is required by the Anthropic adapter
        but isn't in the canonical schema — must ride in ``extensions.strands``
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
        # Simulate a cross-framework read: build the proto event directly
        # without setting ``extensions["strands"]``.
        events = [
            ToolResultReceived(
                call_id="c1",
                result='[{"text":"ok"}]',
                message_index=0,
                timestamp=TS,
            )
        ]
        [restored] = canonical_to_messages(events)
        assert restored["content"][0]["toolResult"]["status"] == "success"

    def test_tool_result_content_dict_wrapped_for_cross_framework(self) -> None:
        """If a non-Strands writer (e.g. MAF) put the raw structured tool
        return in ``ToolResultReceived.result`` (a JSON-encoded dict, not a
        list of content blocks), Strands' deserialiser must wrap it as
        ``[{"json": <dict>}]`` so the model adapter doesn't trip when
        iterating ``content`` as a dict.

        See https://github.com/kurrent-io/kurrent-agents/issues/58.
        """
        events = [
            ToolResultReceived(
                call_id="c1",
                # MAF-style: raw structured result, not pre-wrapped as a
                # list of Strands content blocks.
                result='{"status":"success","city":"Tokyo","temperature_c":22}',
                message_index=0,
                timestamp=TS,
            )
        ]
        [restored] = canonical_to_messages(events)
        content = restored["content"][0]["toolResult"]["content"]
        assert isinstance(content, list)
        assert content == [
            {"json": {"status": "success", "city": "Tokyo", "temperature_c": 22}}
        ]

    def test_tool_result_content_string_wrapped_for_cross_framework(self) -> None:
        """A plain-string tool result (e.g. a non-JSON tool return) gets
        wrapped as ``[{"text": <str>}]`` so model adapters still iterate
        a list of blocks."""
        events = [
            ToolResultReceived(
                call_id="c1",
                result="just a plain string",
                message_index=0,
                timestamp=TS,
            )
        ]
        [restored] = canonical_to_messages(events)
        content = restored["content"][0]["toolResult"]["content"]
        assert content == [{"text": "just a plain string"}]

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
    def test_image_block_with_uri_preserved_via_extensions(self) -> None:
        """Non-canonical blocks (image / document / etc.) ride in
        ``extensions.strands.non_canonical_blocks`` and restore verbatim."""
        image_block = {
            "image": {"format": "png", "source": {"uri": "s3://bucket/img.png"}},
        }
        original = _msg("user", [{"text": "look at this"}, image_block])
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        assert len(events) == 1
        strands_ext = _strands_extension(events[0])
        assert strands_ext["non_canonical_blocks"] == [image_block]

        [restored] = canonical_to_messages(events)
        # Text block first (from canonical), then the image (from extensions).
        assert restored["content"][0] == {"text": "look at this"}
        assert restored["content"][1] == image_block

    def test_image_block_with_inline_bytes_round_trips_losslessly(self) -> None:
        """Bytes-bearing blocks (image source bytes, document data) round-trip
        via the ``__bytes_b64__`` wrapper so ``message_to_canonical`` doesn't
        crash on JSON-incompatible Struct values and same-framework reads
        recover the original ``bytes``.
        """
        image_block = {
            "image": {"format": "png", "source": {"bytes": b"fake-png"}},
        }
        original = _msg("user", [{"text": "see attached"}, image_block])
        events = message_to_canonical(original, message_index=0, timestamp=TS)
        assert len(events) == 1

        [restored] = canonical_to_messages(events)
        assert restored["content"][0] == {"text": "see attached"}
        assert restored["content"][1] == image_block
        # And the bytes are actually bytes after restore (not the wrapper).
        assert isinstance(
            restored["content"][1]["image"]["source"]["bytes"], bytes
        )


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
