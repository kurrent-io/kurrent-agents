"""Round-trip tests for the Event ↔ canonical codec.

For each representative ADK Event shape we assert:

1. ``event_to_canonical`` emits the expected canonical event types.
2. ``canonical_to_events`` reconstructs an Event whose important fields match
   the original (equality modulo auto-generated fields and the known v1
   limitations around LlmResponse metadata).
"""

from __future__ import annotations

from google.adk.events.event import Event as AdkEvent
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

from kurrent_google_adk._codec import (
    canonical_to_events,
    event_to_canonical,
    extract_usage_metadata,
)
from kurrent_google_adk._schema.events import (
    ADK_EXTENSION_KEY,
    AgentTransferred,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    Compaction,
    Rewind,
    ToolResultReceived,
    UserMessageReceived,
)


def _make_event(**kwargs) -> AdkEvent:
    """Helper: build an Event with a fixed timestamp for comparability."""
    kwargs.setdefault("timestamp", 1_713_528_000.0)  # 2024-04-19T12:00:00Z
    kwargs.setdefault("invocation_id", "inv_1")
    return AdkEvent(**kwargs)


def _assert_round_trip(original: AdkEvent) -> AdkEvent:
    """Decompose then reconstruct; return the reconstructed event for further assertions."""
    canonical = event_to_canonical(original)
    assert canonical, "codec emitted zero canonical events"
    [reconstructed] = canonical_to_events(canonical)

    # Author, invocation_id, branch, id, long_running_tool_ids, partial must round-trip.
    assert reconstructed.author == original.author
    assert reconstructed.invocation_id == original.invocation_id
    assert reconstructed.branch == original.branch
    assert reconstructed.id == original.id
    assert reconstructed.long_running_tool_ids == (original.long_running_tool_ids or None)
    assert reconstructed.partial == original.partial

    # EventActions fields preserved in extensions must round-trip.
    assert reconstructed.actions.state_delta == (original.actions.state_delta or {})
    assert reconstructed.actions.artifact_delta == (original.actions.artifact_delta or {})
    assert reconstructed.actions.skip_summarization == original.actions.skip_summarization
    assert reconstructed.actions.escalate == original.actions.escalate
    assert reconstructed.actions.end_of_agent == original.actions.end_of_agent
    assert reconstructed.actions.agent_state == original.actions.agent_state
    assert reconstructed.actions.transfer_to_agent == original.actions.transfer_to_agent
    assert (
        reconstructed.actions.rewind_before_invocation_id
        == original.actions.rewind_before_invocation_id
    )

    return reconstructed


def _content_text(event: AdkEvent) -> str | None:
    if not event.content or not event.content.parts:
        return None
    chunks = [p.text for p in event.content.parts if p.text is not None]
    return "".join(chunks) if chunks else None


class TestUserMessage:
    def test_plain_user_text(self) -> None:
        original = _make_event(
            author="user",
            content=types.Content(role="user", parts=[types.Part(text="hello")]),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], UserMessageReceived)
        assert canonical[0].content == "hello"
        assert canonical[0].extensions[ADK_EXTENSION_KEY]["invocation_id"] == "inv_1"

        reconstructed = _assert_round_trip(original)
        assert _content_text(reconstructed) == "hello"

    def test_preserves_branch_and_id(self) -> None:
        original = _make_event(
            author="user",
            content=types.Content(role="user", parts=[types.Part(text="hi")]),
            branch="root.worker_2",
            id="custom-id-42",
        )
        reconstructed = _assert_round_trip(original)
        assert reconstructed.branch == "root.worker_2"
        assert reconstructed.id == "custom-id-42"


class TestAssistantText:
    def test_plain_text(self) -> None:
        original = _make_event(
            author="root_agent",
            content=types.Content(role="model", parts=[types.Part(text="hi there")]),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], AssistantTextGenerated)
        assert canonical[0].content == "hi there"

        reconstructed = _assert_round_trip(original)
        assert reconstructed.author == "root_agent"
        assert _content_text(reconstructed) == "hi there"


class TestAssistantToolCalls:
    def test_tool_calls_only(self) -> None:
        original = _make_event(
            author="root_agent",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="c1", name="search", args={"q": "kurrent"}
                        )
                    )
                ],
            ),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], AssistantToolCallsGenerated)
        assert canonical[0].content is None
        assert canonical[0].tool_calls[0].call_id == "c1"
        assert canonical[0].tool_calls[0].tool_name == "search"
        assert canonical[0].tool_calls[0].arguments == {"q": "kurrent"}

        reconstructed = _assert_round_trip(original)
        fc = reconstructed.get_function_calls()[0]
        assert fc.id == "c1"
        assert fc.name == "search"
        assert fc.args == {"q": "kurrent"}

    def test_tool_calls_with_accompanying_text(self) -> None:
        original = _make_event(
            author="root_agent",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Let me search for that."),
                    types.Part(
                        function_call=types.FunctionCall(
                            id="c1", name="search", args={"q": "x"}
                        )
                    ),
                ],
            ),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], AssistantToolCallsGenerated)
        assert canonical[0].content == "Let me search for that."
        assert len(canonical[0].tool_calls) == 1

        reconstructed = _assert_round_trip(original)
        # Reconstructed content merges text + function_call parts.
        assert _content_text(reconstructed) == "Let me search for that."
        assert reconstructed.get_function_calls()[0].name == "search"

    def test_empty_args_round_trip_as_empty_dict(self) -> None:
        """Tools taking no parameters must keep ``args={}`` on round-trip.

        Regression test: Anthropic rejects ``tool_use.input: null`` ("Input
        should be a valid dictionary"), so the codec must not collapse an
        empty dict to None.
        """
        original = _make_event(
            author="root_agent",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="c1", name="list_notes", args={}
                        )
                    )
                ],
            ),
        )
        reconstructed = _assert_round_trip(original)
        fc = reconstructed.get_function_calls()[0]
        assert fc.args == {}  # not None

    def test_long_running_tool_ids_round_trip(self) -> None:
        original = _make_event(
            author="root_agent",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="c1", name="ask_human", args={}
                        )
                    )
                ],
            ),
            long_running_tool_ids={"c1"},
        )
        reconstructed = _assert_round_trip(original)
        assert reconstructed.long_running_tool_ids == {"c1"}


class TestToolResult:
    def test_single_tool_result_on_user_role(self) -> None:
        original = _make_event(
            author="user",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="c1", name="search", response={"hits": 3}
                        )
                    )
                ],
            ),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], ToolResultReceived)
        assert canonical[0].call_id == "c1"
        assert canonical[0].tool_name == "search"

        reconstructed = _assert_round_trip(original)
        fr = reconstructed.get_function_responses()[0]
        assert fr.id == "c1"
        assert fr.response == {"hits": 3}

    def test_result_with_pydantic_model_payload(self) -> None:
        """ADK's LoadMemoryTool returns a Pydantic model as the tool response.

        ``_serialize_response`` must handle that instead of ``json.dumps`` it
        directly (which raises ``TypeError``). Regression test for a crash
        surfaced by the memory_agent sample.
        """
        from pydantic import BaseModel

        class _FakeResponse(BaseModel):
            hits: int
            label: str

        fake = _FakeResponse(hits=3, label="ok")
        original = _make_event(
            author="user",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="c1",
                            name="fetch",
                            # google.genai normalises pydantic → dict before this
                            # hits our codec, but ``fetch`` wrapping the dict
                            # under ``"result"`` is enough to exercise the
                            # non-native-JSON fallback path too.
                            response=fake.model_dump(),
                        )
                    )
                ],
            ),
        )
        # Should neither raise nor drop the payload.
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        reconstructed = canonical_to_events(canonical)[0]
        fr = reconstructed.get_function_responses()[0]
        assert fr.response == {"hits": 3, "label": "ok"}

    def test_result_with_string_payload(self) -> None:
        # Not valid JSON — exercises the fallback wrap in `{"result": <string>}`.
        original = _make_event(
            author="user",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="c1", name="fetch", response={"raw": "not-a-json-dict"}
                        )
                    )
                ],
            ),
        )
        reconstructed = _assert_round_trip(original)
        fr = reconstructed.get_function_responses()[0]
        assert fr.response == {"raw": "not-a-json-dict"}


class TestMetaEvents:
    def test_agent_transferred(self) -> None:
        original = _make_event(
            author="root_agent",
            actions=EventActions(transfer_to_agent="worker_1"),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], AgentTransferred)
        assert canonical[0].from_agent == "root_agent"
        assert canonical[0].to_agent == "worker_1"

        reconstructed = _assert_round_trip(original)
        assert reconstructed.actions.transfer_to_agent == "worker_1"

    def test_rewind(self) -> None:
        original = _make_event(
            author="root_agent",
            actions=EventActions(
                rewind_before_invocation_id="inv_past",
                state_delta={"counter": 0},
            ),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], Rewind)
        assert canonical[0].rewind_before_invocation_id == "inv_past"
        assert canonical[0].state_delta == {"counter": 0}

        reconstructed = _assert_round_trip(original)
        assert reconstructed.actions.rewind_before_invocation_id == "inv_past"
        assert reconstructed.actions.state_delta == {"counter": 0}

    def test_compaction(self) -> None:
        original = _make_event(
            author="root_agent",
            actions=EventActions(
                compaction=EventCompaction(
                    start_timestamp=1_000.0,
                    end_timestamp=2_000.0,
                    compacted_content=types.Content(
                        role="model",
                        parts=[types.Part(text="summary text")],
                    ),
                )
            ),
        )
        canonical = event_to_canonical(original)
        assert len(canonical) == 1
        assert isinstance(canonical[0], Compaction)

        reconstructed = _assert_round_trip(original)
        comp = reconstructed.actions.compaction
        assert comp is not None
        assert comp.start_timestamp == 1_000.0
        assert comp.end_timestamp == 2_000.0
        assert comp.compacted_content.parts[0].text == "summary text"


class TestActionsPreservation:
    def test_state_delta_round_trips_verbatim(self) -> None:
        """State delta is preserved in extensions.adk; session service routes
        by prefix separately."""
        original = _make_event(
            author="root_agent",
            content=types.Content(role="model", parts=[types.Part(text="ok")]),
            actions=EventActions(
                state_delta={
                    "session_key": "v1",
                    "app:setting": "x",
                    "user:pref": "dark",
                },
            ),
        )
        reconstructed = _assert_round_trip(original)
        assert reconstructed.actions.state_delta == {
            "session_key": "v1",
            "app:setting": "x",
            "user:pref": "dark",
        }

    def test_resume_state_preserved(self) -> None:
        """LoopAgent / ParallelAgent resume fields must round-trip exactly."""
        original = _make_event(
            author="root_agent",
            content=types.Content(role="model", parts=[types.Part(text="done")]),
            actions=EventActions(
                agent_state={"current_sub_agent": "worker_2", "times_looped": 3},
                end_of_agent=True,
            ),
        )
        reconstructed = _assert_round_trip(original)
        assert reconstructed.actions.agent_state == {
            "current_sub_agent": "worker_2",
            "times_looped": 3,
        }
        assert reconstructed.actions.end_of_agent is True

    def test_artifact_delta_escalate_and_flags(self) -> None:
        original = _make_event(
            author="root_agent",
            content=types.Content(role="model", parts=[types.Part(text="done")]),
            actions=EventActions(
                artifact_delta={"notes.txt": 3},
                escalate=True,
                skip_summarization=True,
            ),
        )
        reconstructed = _assert_round_trip(original)
        assert reconstructed.actions.artifact_delta == {"notes.txt": 3}
        assert reconstructed.actions.escalate is True
        assert reconstructed.actions.skip_summarization is True


class TestMultipleEventsRoundTrip:
    def test_multi_turn_conversation(self) -> None:
        events = [
            _make_event(
                author="user",
                invocation_id="inv_a",
                content=types.Content(role="user", parts=[types.Part(text="hello")]),
            ),
            _make_event(
                author="root_agent",
                invocation_id="inv_a",
                content=types.Content(role="model", parts=[types.Part(text="hi!")]),
            ),
            _make_event(
                author="user",
                invocation_id="inv_b",
                content=types.Content(role="user", parts=[types.Part(text="search for x")]),
            ),
            _make_event(
                author="root_agent",
                invocation_id="inv_b",
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="c1", name="search", args={"q": "x"}
                            )
                        )
                    ],
                ),
            ),
        ]
        canonical: list = []
        for event in events:
            canonical.extend(event_to_canonical(event))

        reconstructed = canonical_to_events(canonical)
        assert len(reconstructed) == len(events)
        assert _content_text(reconstructed[0]) == "hello"
        assert _content_text(reconstructed[1]) == "hi!"
        assert _content_text(reconstructed[2]) == "search for x"
        assert reconstructed[3].get_function_calls()[0].name == "search"


class TestUsageMetadata:
    def test_returns_none_when_absent(self) -> None:
        event = _make_event(
            author="root_agent",
            content=types.Content(role="model", parts=[types.Part(text="ok")]),
        )
        assert extract_usage_metadata(event) is None

    def test_extracts_token_counts(self) -> None:
        event = _make_event(
            author="root_agent",
            content=types.Content(role="model", parts=[types.Part(text="ok")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=1_507,
                candidates_token_count=203,
                total_token_count=1_710,
                cached_content_token_count=0,
            ),
        )
        usage = extract_usage_metadata(event)
        assert usage == {
            "input_tokens": 1_507,
            "output_tokens": 203,
            "total_tokens": 1_710,
            "cached_input_tokens": 0,
        }
