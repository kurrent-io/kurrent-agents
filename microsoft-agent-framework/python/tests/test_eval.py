"""Unit tests for the eval module — :class:`EvalRunner`, :func:`read_session_turns`,
and the :func:`llm_judge` built-in scorer.

Mirrors the .NET ``EvalRunnerTests`` / ``SessionTurnReaderTests`` against an
in-memory fake client so the suite stays hermetic (same pattern as
``test_chat_history.py``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from agent_framework import ChatResponse, Message
from google.protobuf.message import Message as ProtoMessage
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)
from kurrentdbclient import NewEvent, RecordedEvent
from kurrentdbclient.exceptions import NotFoundError

from kurrent_agent_framework import (
    EvalRunner,
    ScoredTurn,
    Turn,
    llm_judge,
    read_session_turns,
    serialization,
)

TS = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


class _FakeResponse:
    def __init__(self, events: list[RecordedEvent]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[RecordedEvent]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[RecordedEvent]:
        for event in self._events:
            yield event


class FakeClient:
    """Minimal in-memory ``AsyncKurrentDBClient`` substitute for unit tests."""

    def __init__(self) -> None:
        self.streams: dict[str, list[RecordedEvent]] = {}

    async def append_to_stream(
        self,
        *,
        stream_name: str,
        current_version: Any,
        events: list[NewEvent],
    ) -> None:
        bucket = self.streams.setdefault(stream_name, [])
        start = len(bucket)
        for idx, new in enumerate(events):
            bucket.append(
                RecordedEvent(
                    type=new.type,
                    data=new.data,
                    metadata=new.metadata,
                    content_type="application/json",
                    id=new.id,
                    stream_name=stream_name,
                    stream_position=start + idx,
                    commit_position=start + idx,
                    prepare_position=start + idx,
                    recorded_at=datetime.now(UTC),
                )
            )

    async def read_stream(self, stream_name: str, **_: Any) -> _FakeResponse:
        if stream_name not in self.streams:
            raise NotFoundError(f"stream {stream_name!r} not found")
        return _FakeResponse(list(self.streams[stream_name]))


async def _seed_session(
    client: FakeClient,
    session_id: str,
    *events: ProtoMessage,
    metadata: Sequence[dict[str, Any] | None] | None = None,
) -> None:
    """Append serialized canonical events to the session stream for ``session_id``."""
    metadatas = list(metadata) if metadata else [None] * len(events)
    if len(metadatas) != len(events):
        raise ValueError("metadata sequence must match events")
    new_events = [
        serialization.serialize(event, metadata=md) for event, md in zip(events, metadatas, strict=True)
    ]
    await client.append_to_stream(
        stream_name=f"AgentSession-{session_id}",
        current_version=None,
        events=new_events,
    )


def _eval_stream(client: FakeClient) -> tuple[str, list[RecordedEvent]]:
    """Return the lone ``EvalRun-*`` stream in the fake client."""
    eval_streams = {k: v for k, v in client.streams.items() if k.startswith("EvalRun-")}
    assert len(eval_streams) == 1, f"expected one eval stream, got {list(eval_streams)}"
    name, events = next(iter(eval_streams.items()))
    return name, events


def _usage(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {"$usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


# --- read_session_turns ------------------------------------------------------


async def test_read_session_turns_on_missing_stream_returns_empty() -> None:
    client = FakeClient()

    turns = await read_session_turns(client, "no-such-session")  # type: ignore[arg-type]

    assert turns == []


async def test_read_session_turns_single_pair_yields_one_turn() -> None:
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        SessionStarted(agent_name="agent", model="model", timestamp=TS),
        UserMessageReceived(content="hello", message_id="m-1", message_index=0, timestamp=TS),
        AssistantTextGenerated(content="hi back", message_id="m-2", message_index=1, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    assert turns[0].index == 0
    assert turns[0].user_input == "hello"
    assert turns[0].assistant_output == "hi back"
    assert turns[0].tool_calls == ()


async def test_read_session_turns_segments_on_user_message() -> None:
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="Q1", message_index=0, timestamp=TS),
        AssistantTextGenerated(content="A1", message_index=1, timestamp=TS),
        UserMessageReceived(content="Q2", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="A2", message_index=3, timestamp=TS),
        UserMessageReceived(content="Q3", message_index=4, timestamp=TS),
        AssistantTextGenerated(content="A3", message_index=5, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert [t.user_input for t in turns] == ["Q1", "Q2", "Q3"]
    assert [t.assistant_output for t in turns] == ["A1", "A2", "A3"]
    assert [t.index for t in turns] == [0, 1, 2]


async def test_read_session_turns_correlates_tool_call_and_result() -> None:
    client = FakeClient()
    tool_call = ToolCallInfo(call_id="call-1", tool_name="get_weather")
    tool_call.arguments.update({"city": "Paris"})
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="weather?", message_index=0, timestamp=TS),
        AssistantToolCallsGenerated(
            tool_calls=[tool_call],
            message_index=1,
            timestamp=TS,
        ),
        ToolResultReceived(call_id="call-1", tool_name="get_weather", result="sunny", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="it's sunny", message_index=3, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    assert len(turns[0].tool_calls) == 1
    tc = turns[0].tool_calls[0]
    assert tc.name == "get_weather"
    assert tc.arguments == '{"city":"Paris"}'
    assert tc.result == "sunny"


async def test_read_session_turns_aggregates_usage_from_metadata() -> None:
    """Usage on both ``AssistantToolCallsGenerated`` and ``AssistantTextGenerated``
    events within a turn must sum into the turn's ``input_tokens`` /
    ``output_tokens`` totals — same accumulation rule as the .NET reader."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="hi", message_index=0, timestamp=TS),
        AssistantToolCallsGenerated(
            tool_calls=[ToolCallInfo(call_id="call-1", tool_name="t")],
            message_index=1,
            timestamp=TS,
        ),
        ToolResultReceived(call_id="call-1", tool_name="t", result="r", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="done", message_index=3, timestamp=TS),
        metadata=[None, _usage(10, 5), None, _usage(20, 7)],
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    assert turns[0].input_tokens == 30
    assert turns[0].output_tokens == 12


async def test_read_session_turns_user_without_assistant_still_produces_turn() -> None:
    """Mid-flight session: user asked, agent hasn't responded yet."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="pending", message_index=0, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    assert turns[0].user_input == "pending"
    assert turns[0].assistant_output is None


async def test_read_session_turns_skips_unknown_event_types() -> None:
    """Non-canonical event types (no entry in ``EVENT_TYPE_BY_NAME``) must not
    derail the reader — same passthrough rule serialization.deserialize uses."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="Q", message_index=0, timestamp=TS),
    )
    # Splice an unknown-type event in between known events.
    await client.append_to_stream(
        stream_name="AgentSession-s1",
        current_version=None,
        events=[NewEvent(type="TotallyUnknown", data=b"{}")],
    )
    await _seed_session(
        client,
        "s1",
        AssistantTextGenerated(content="A", message_index=1, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    assert turns[0].user_input == "Q"
    assert turns[0].assistant_output == "A"


# --- EvalRunner.run ---------------------------------------------------------


async def test_run_computes_average_score_and_returns_scored_turns() -> None:
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="q1", message_index=0, timestamp=TS),
        AssistantTextGenerated(content="a1", message_index=1, timestamp=TS),
        UserMessageReceived(content="q2", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="a2", message_index=3, timestamp=TS),
    )

    scores = [1.0, 0.5]
    labels = ["good", "acceptable"]
    reasons = ["perfect", "could be better"]

    async def fixed(turn: Turn) -> ScoredTurn:
        return ScoredTurn(turn=turn, score=scores[turn.index], label=labels[turn.index], reason=reasons[turn.index])

    runner = EvalRunner(client)  # type: ignore[arg-type]
    result = await runner.run(session_id="s1", scorer_name="fixed", criteria="testing", scorer=fixed)

    assert result.session_id == "s1"
    assert len(result.scored_turns) == 2
    assert result.average_score == 0.75
    assert result.scored_turns[0].score == 1.0
    assert result.scored_turns[1].score == 0.5


async def test_run_aggregates_token_totals_from_turn_metadata() -> None:
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="q1", message_index=0, timestamp=TS),
        AssistantTextGenerated(content="a1", message_index=1, timestamp=TS),
        UserMessageReceived(content="q2", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="a2", message_index=3, timestamp=TS),
        metadata=[None, _usage(10, 5), None, _usage(20, 8)],
    )

    async def constant(turn: Turn) -> ScoredTurn:
        return ScoredTurn(turn=turn, score=1.0, label=None, reason=None)

    result = await EvalRunner(client).run(  # type: ignore[arg-type]
        session_id="s1", scorer_name="fixed", criteria="testing", scorer=constant
    )

    assert result.total_input_tokens == 30
    assert result.total_output_tokens == 13


async def test_run_emits_event_sequence_to_eval_run_stream() -> None:
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="q", message_index=0, timestamp=TS),
        AssistantTextGenerated(content="a", message_index=1, timestamp=TS),
    )

    async def fixed(turn: Turn) -> ScoredTurn:
        return ScoredTurn(turn=turn, score=0.9, label="good", reason="solid")

    await EvalRunner(client).run(  # type: ignore[arg-type]
        session_id="s1", scorer_name="my-scorer", criteria="helpfulness", scorer=fixed
    )

    name, events = _eval_stream(client)
    assert name.startswith("EvalRun-")
    assert [e.type for e in events] == ["EvalRunStarted", "TurnScored", "EvalRunCompleted"]

    started = json.loads(events[0].data)
    assert started["scorer"] == "my-scorer"
    assert started["criteria"] == "helpfulness"
    assert started["session_id"] == "s1"

    # Proto3 JSON canonical form omits default-valued non-optional scalars
    # (turn_index = 0, turns_scored = 0); treat missing as default per the
    # proto3 contract.
    scored = json.loads(events[1].data)
    assert scored.get("turn_index", 0) == 0
    assert scored["score"] == 0.9
    assert scored["score_label"] == "good"
    assert scored["input"] == "q"
    assert scored["output"] == "a"

    completed = json.loads(events[2].data)
    assert completed.get("turns_scored", 0) == 1
    assert completed["average_score"] == 0.9


async def test_run_with_no_turns_still_writes_started_and_completed() -> None:
    """No session stream → reader returns []. The runner must still bracket
    the run with ``EvalRunStarted`` / ``EvalRunCompleted`` so the eval is a
    durable record (downstream consumers can tell ``no turns scored`` from
    ``run never happened``)."""
    client = FakeClient()

    async def noop(turn: Turn) -> ScoredTurn:
        return ScoredTurn(turn=turn, score=1.0, label=None, reason=None)

    result = await EvalRunner(client).run(  # type: ignore[arg-type]
        session_id="empty", scorer_name="noop", criteria="none", scorer=noop
    )

    assert result.scored_turns == ()
    assert result.average_score == 0.0
    _, events = _eval_stream(client)
    assert [e.type for e in events] == ["EvalRunStarted", "EvalRunCompleted"]


# --- llm_judge --------------------------------------------------------------


class StubChatClient:
    """Minimal :class:`SupportsChatGetResponse` substitute returning fixed text."""

    def __init__(self, response_text: str) -> None:
        self._text = response_text
        self.calls: list[Sequence[Message]] = []

    async def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: bool = False,
        options: Any = None,
        **_: Any,
    ) -> ChatResponse:
        self.calls.append(messages)
        return ChatResponse(messages=[Message("assistant", [self._text])])


def _empty_turn() -> Turn:
    return Turn(
        index=0,
        user_input="q",
        assistant_output="a",
        tool_calls=(),
        input_tokens=None,
        output_tokens=None,
    )


async def test_llm_judge_parses_valid_json_response() -> None:
    client = StubChatClient('{"score": 0.8, "label": "good", "reason": "clear"}')
    judge = llm_judge(client, "accuracy")

    scored = await judge(_empty_turn())

    assert scored.score == 0.8
    assert scored.label == "good"
    assert scored.reason == "clear"


async def test_llm_judge_malformed_json_falls_back_to_half_score() -> None:
    client = StubChatClient("not JSON at all")
    judge = llm_judge(client, "accuracy")

    scored = await judge(_empty_turn())

    assert scored.score == 0.5
    assert scored.label == "parse_error"
    assert scored.reason is not None


async def test_llm_judge_missing_optional_fields_score_still_readable() -> None:
    client = StubChatClient('{"score": 0.42}')
    judge = llm_judge(client, "accuracy")

    scored = await judge(_empty_turn())

    assert scored.score == 0.42
    assert scored.label is None
    assert scored.reason is None


async def test_llm_judge_passes_model_through_options() -> None:
    """``model`` kwarg should land in ``ChatOptions.model`` so callers can
    pin a different judge model without touching the chat client config."""
    received_options: list[Any] = []

    class CapturingClient(StubChatClient):
        async def get_response(  # type: ignore[override]
            self,
            messages: Sequence[Message],
            *,
            stream: bool = False,
            options: Any = None,
            **kwargs: Any,
        ) -> ChatResponse:
            received_options.append(options)
            return await super().get_response(messages, stream=stream, options=options)

    client = CapturingClient('{"score": 1.0}')
    judge = llm_judge(client, "accuracy", model="gpt-judge")

    await judge(_empty_turn())

    assert received_options == [{"model": "gpt-judge"}]


# --- Regression coverage for review-fix issues -----------------------------


async def test_run_brackets_completed_event_when_scorer_raises() -> None:
    """``EvalRunCompleted`` is the terminal marker downstream consumers rely
    on. Even when the scorer raises mid-run, the bracketing must hold and
    the original error must propagate to the caller."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="q1", message_index=0, timestamp=TS),
        AssistantTextGenerated(content="a1", message_index=1, timestamp=TS),
        UserMessageReceived(content="q2", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="a2", message_index=3, timestamp=TS),
    )

    class BoomError(RuntimeError):
        pass

    async def explode_on_second(turn: Turn) -> ScoredTurn:
        if turn.index == 1:
            raise BoomError("scorer broke")
        return ScoredTurn(turn=turn, score=1.0, label="good", reason=None)

    with pytest.raises(BoomError):
        await EvalRunner(client).run(  # type: ignore[arg-type]
            session_id="s1",
            scorer_name="boom",
            criteria="any",
            scorer=explode_on_second,
        )

    _, events = _eval_stream(client)
    types = [e.type for e in events]
    # Started, one TurnScored (the first turn succeeded), then Completed.
    assert types == ["EvalRunStarted", "TurnScored", "EvalRunCompleted"]
    completed = json.loads(events[-1].data)
    assert completed["turns_scored"] == 1
    assert completed["average_score"] == 1.0


async def test_read_session_turns_correlates_results_by_call_id_out_of_order() -> None:
    """Tool-result events may arrive in a different order than the original
    calls. Schema-level ``call_id`` is the correlation key — positional
    back-walk would attribute results to the wrong tool call."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="weather + time?", message_index=0, timestamp=TS),
        AssistantToolCallsGenerated(
            tool_calls=[
                ToolCallInfo(call_id="weather-1", tool_name="GetWeather"),
                ToolCallInfo(call_id="time-1", tool_name="GetTime"),
            ],
            message_index=1,
            timestamp=TS,
        ),
        # Results arrive in reverse order.
        ToolResultReceived(call_id="time-1", tool_name="GetTime", result="12:00", message_index=2, timestamp=TS),
        ToolResultReceived(
            call_id="weather-1", tool_name="GetWeather", result="Sunny", message_index=3, timestamp=TS
        ),
        AssistantTextGenerated(content="Done", message_index=4, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    by_name = {tc.name: tc for tc in turns[0].tool_calls}
    assert by_name["GetWeather"].result == "Sunny"
    assert by_name["GetTime"].result == "12:00"


async def test_read_session_turns_user_with_null_content_still_flushes() -> None:
    """``UserMessageReceived.content`` is ``str | None`` per the schema. A
    null user message must still open a turn so the assistant events that
    follow it land in their own turn instead of being silently lost when
    the next user message arrives."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content=None, message_index=0, timestamp=TS),
        AssistantTextGenerated(content="hi anyway", message_index=1, timestamp=TS),
        UserMessageReceived(content="follow up", message_index=2, timestamp=TS),
        AssistantTextGenerated(content="reply", message_index=3, timestamp=TS),
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 2
    assert turns[0].user_input is None
    assert turns[0].assistant_output == "hi anyway"
    assert turns[1].user_input == "follow up"
    assert turns[1].assistant_output == "reply"


async def test_read_session_turns_aggregates_thinking_usage() -> None:
    """``$usage`` rides on every assistant event including
    ``AssistantThinkingGenerated`` (schema v2 §3.4); thinking tokens must
    count toward the turn's token totals."""
    client = FakeClient()
    await _seed_session(
        client,
        "s1",
        UserMessageReceived(content="hi", message_index=0, timestamp=TS),
        AssistantThinkingGenerated(content="...", message_index=1, timestamp=TS),
        AssistantTextGenerated(content="answer", message_index=2, timestamp=TS),
        metadata=[None, _usage(100, 0), _usage(20, 7)],
    )

    turns = await read_session_turns(client, "s1")  # type: ignore[arg-type]

    assert len(turns) == 1
    assert turns[0].input_tokens == 120
    assert turns[0].output_tokens == 7


async def test_llm_judge_normalises_none_inputs_in_prompt() -> None:
    """``Turn.user_input`` / ``Turn.assistant_output`` are optional. The
    judge must not embed the literal string ``"None"`` into the prompt for
    incomplete turns."""
    seen_prompts: list[str] = []

    class PromptCapturingClient(StubChatClient):
        async def get_response(  # type: ignore[override]
            self,
            messages: Sequence[Message],
            *,
            stream: bool = False,
            options: Any = None,
            **kwargs: Any,
        ) -> ChatResponse:
            seen_prompts.append(messages[0].text or "")
            return await super().get_response(messages, stream=stream, options=options)

    client = PromptCapturingClient('{"score": 0.0}')
    judge = llm_judge(client, "accuracy")

    turn = Turn(
        index=0,
        user_input=None,
        assistant_output=None,
        tool_calls=(),
        input_tokens=None,
        output_tokens=None,
    )
    await judge(turn)

    assert seen_prompts, "judge should have been invoked"
    prompt = seen_prompts[0]
    assert "None" not in prompt, prompt
