"""Replay-and-score evaluations for agent sessions stored in KurrentDB.

Mirrors ``Kurrent.AgentFramework.Eval.EvalRunner`` (.NET):

* :func:`read_session_turns` reads an ``AgentSession-{id}`` stream and groups
  canonical events into conversation :class:`Turn` s
  (``user input → tool calls → assistant output``).
* :class:`EvalRunner` scores each turn with a user-supplied async scorer and
  writes ``EvalRunStarted`` → ``TurnScored*`` → ``EvalRunCompleted`` to a
  fresh ``EvalRun-{id}`` stream so Kapacitor and the .NET side read the same
  shape.
* :func:`llm_judge` is the one built-in scorer — wraps a chat client and
  parses a ``{score, label, reason}`` JSON response.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from agent_framework import ChatResponse, Message, SupportsChatGetResponse
from google.protobuf.json_format import MessageToDict, ParseError
from google.protobuf.struct_pb2 import Struct
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    EvalRunCompleted,
    EvalRunStarted,
    ToolResultReceived,
    TurnScored,
    UserMessageReceived,
    agent_session_stream,
    eval_run_stream,
)
from kurrentdbclient import AsyncKurrentDBClient, RecordedEvent, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import serialization

logger = logging.getLogger("kurrent_agent_framework.eval")


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation captured during a turn.

    ``call_id`` is the schema-level correlation key between
    :class:`AssistantToolCallsGenerated` and :class:`ToolResultReceived`, used
    to attach a result to its originating call even when results arrive
    out of order.
    """

    name: str
    arguments: str | None
    result: str | None
    is_error: bool
    call_id: str | None = None


@dataclass(frozen=True)
class Turn:
    """A user/assistant exchange extracted from a session stream."""

    index: int
    user_input: str | None
    assistant_output: str | None
    tool_calls: tuple[ToolCall, ...]
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class ScoredTurn:
    """Result of running a scorer over a single :class:`Turn`."""

    turn: Turn
    score: float
    label: str | None
    reason: str | None


@dataclass(frozen=True)
class EvalResult:
    """Aggregate result of an eval run across one session."""

    session_id: str
    scored_turns: tuple[ScoredTurn, ...]
    average_score: float
    total_input_tokens: int | None
    total_output_tokens: int | None


Scorer = Callable[[Turn], Awaitable[ScoredTurn]]
"""Async scoring function: takes a :class:`Turn`, returns a :class:`ScoredTurn`."""


async def read_session_turns(
    client: AsyncKurrentDBClient,
    session_id: str,
) -> list[Turn]:
    """Read ``AgentSession-{session_id}`` and group canonical events into turns.

    Each turn is bounded by a ``UserMessageReceived``: subsequent
    ``AssistantToolCallsGenerated`` / ``ToolResultReceived`` /
    ``AssistantTextGenerated`` events accumulate into the open turn until the
    next user message starts a new one. Returns an empty list when the stream
    does not exist (e.g. no events have been written yet).
    """
    stream = agent_session_stream(session_id)
    turns: list[Turn] = []

    turn_open = False
    current_input: str | None = None
    current_output: str | None = None
    current_tools: list[ToolCall] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    turn_index = 0

    def flush() -> None:
        nonlocal turn_open, current_input, current_output, current_tools
        nonlocal input_tokens, output_tokens, turn_index
        turns.append(
            Turn(
                index=turn_index,
                user_input=current_input,
                assistant_output=current_output,
                tool_calls=tuple(current_tools),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        turn_index += 1
        turn_open = False
        current_input = None
        current_output = None
        current_tools = []
        input_tokens = None
        output_tokens = None

    try:
        response = await client.read_stream(stream)
        async for recorded in response:
            try:
                event = serialization.deserialize(recorded)
            except (json.JSONDecodeError, ParseError, UnicodeDecodeError) as exc:
                # Match the defensive behaviour of ``KurrentDBHistoryProvider``:
                # a single malformed event must not abort the whole replay.
                logger.warning(
                    "Skipping malformed canonical event at %s:%s: %r",
                    recorded.stream_name,
                    recorded.stream_position,
                    exc,
                )
                continue
            if event is None:
                continue

            if isinstance(event, UserMessageReceived):
                # ``UserMessageReceived.content`` is ``str | None``; a null user
                # message still opens a turn boundary, so track openness with
                # an explicit flag rather than ``current_input is not None``.
                if turn_open:
                    flush()
                turn_open = True
                current_input = event.content if event.HasField("content") else None
            elif isinstance(event, AssistantToolCallsGenerated):
                current_tools.extend(
                    ToolCall(
                        name=tc.tool_name,
                        arguments=_arguments_to_str(tc.arguments) if tc.HasField("arguments") else None,
                        result=None,
                        is_error=False,
                        call_id=tc.call_id or None,
                    )
                    for tc in event.tool_calls
                )
                input_tokens, output_tokens = _accumulate_usage(
                    recorded, input_tokens, output_tokens
                )
            elif isinstance(event, ToolResultReceived):
                _attach_tool_result(current_tools, event)
            elif isinstance(event, AssistantTextGenerated):
                current_output = event.content if event.HasField("content") else None
                input_tokens, output_tokens = _accumulate_usage(
                    recorded, input_tokens, output_tokens
                )
            elif isinstance(event, AssistantThinkingGenerated):
                # Schema v2 §3.4: ``$usage`` rides on every assistant event,
                # including thinking. The thinking content stays off the
                # ``Turn`` (it isn't part of the user-visible exchange) but
                # its token usage must contribute to per-turn totals.
                input_tokens, output_tokens = _accumulate_usage(
                    recorded, input_tokens, output_tokens
                )
    except NotFoundError:
        return turns

    if turn_open:
        flush()

    return turns


def _attach_tool_result(current_tools: list[ToolCall], event: ToolResultReceived) -> None:
    """Attach a ``ToolResultReceived`` to its originating ``ToolCall``.

    Prefers the schema-level ``call_id`` correlation key so out-of-order
    results land on the right call. Falls back to the most recent unresolved
    call only when ``call_id`` is missing on either side (older streams or
    integrations that didn't populate it).
    """
    result = event.result if event.HasField("result") else None
    if event.call_id:
        for i in range(len(current_tools) - 1, -1, -1):
            if current_tools[i].call_id == event.call_id and current_tools[i].result is None:
                current_tools[i] = replace(current_tools[i], result=result)
                return
    for i in range(len(current_tools) - 1, -1, -1):
        if current_tools[i].result is None and not current_tools[i].call_id:
            current_tools[i] = replace(current_tools[i], result=result)
            return


def _arguments_to_str(arguments: Struct) -> str | None:
    """Render tool-call arguments (``google.protobuf.Struct``) as a compact JSON
    string for display."""
    return json.dumps(MessageToDict(arguments, preserving_proto_field_name=True), separators=(",", ":"))


def _accumulate_usage(
    recorded: RecordedEvent,
    input_tokens: int | None,
    output_tokens: int | None,
) -> tuple[int | None, int | None]:
    """Add ``$usage`` token counts from event metadata into the running totals."""
    md = serialization.read_metadata(recorded)
    if not md:
        return input_tokens, output_tokens
    usage = md.get("$usage")
    if not isinstance(usage, dict):
        return input_tokens, output_tokens
    inp = usage.get("input_tokens")
    outp = usage.get("output_tokens")
    if isinstance(inp, int):
        input_tokens = (input_tokens or 0) + inp
    if isinstance(outp, int):
        output_tokens = (output_tokens or 0) + outp
    return input_tokens, output_tokens


def _sum_optional(values: Iterable[int | None]) -> int | None:
    """Sum a stream of optional ints; return ``None`` if no value is present."""
    total: int | None = None
    for v in values:
        if v is None:
            continue
        total = (total or 0) + v
    return total


class EvalRunner:
    """Score every turn in a session and persist the results.

    Reads turns from an ``AgentSession-{id}`` stream, scores each one with a
    caller-supplied :data:`Scorer`, and writes an ``EvalRunStarted`` →
    ``TurnScored*`` → ``EvalRunCompleted`` sequence to a fresh ``EvalRun-{id}``
    stream. Wire-compatible with the MAF .NET ``EvalRunner`` so Kapacitor (and
    any other consumer) reads either side identically.
    """

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client

    async def run(
        self,
        *,
        session_id: str,
        scorer_name: str,
        criteria: str,
        scorer: Scorer,
    ) -> EvalResult:
        """Score every turn and persist the bracketed event sequence.

        ``EvalRunCompleted`` is appended in a ``finally`` block so the stream
        is always terminally bracketed, even when a scorer raises mid-run —
        downstream consumers can rely on completion as a terminal marker.
        ``turns_scored`` / ``average_score`` reflect what completed before any
        failure, and the original exception propagates to the caller.
        """
        turns = await read_session_turns(self._client, session_id)
        eval_id = uuid.uuid4().hex
        stream = eval_run_stream(eval_id)
        now = datetime.now(UTC)

        await self._append(
            stream,
            EvalRunStarted(
                session_id=session_id,
                scorer=scorer_name,
                criteria=criteria,
                timestamp=now,
            ),
        )

        scored_turns: list[ScoredTurn] = []
        try:
            for turn in turns:
                scored = await scorer(turn)
                scored_turns.append(scored)
                await self._append(
                    stream,
                    TurnScored(
                        session_id=session_id,
                        turn_index=turn.index,
                        input=turn.user_input,
                        output=turn.assistant_output,
                        score=scored.score,
                        score_label=scored.label,
                        reason=scored.reason,
                        timestamp=datetime.now(UTC),
                    ),
                )
        finally:
            average = (
                sum(s.score for s in scored_turns) / len(scored_turns)
                if scored_turns
                else 0.0
            )
            try:
                await self._append(
                    stream,
                    EvalRunCompleted(
                        session_id=session_id,
                        turns_scored=len(scored_turns),
                        average_score=average,
                        total_cost=None,
                        timestamp=datetime.now(UTC),
                    ),
                )
            except Exception:
                # Don't mask the original failure with a Completed-write error.
                logger.exception(
                    "Failed to append EvalRunCompleted for %s; original error (if any) will still propagate",
                    stream,
                )

        return EvalResult(
            session_id=session_id,
            scored_turns=tuple(scored_turns),
            average_score=average,
            total_input_tokens=_sum_optional(t.input_tokens for t in turns),
            total_output_tokens=_sum_optional(t.output_tokens for t in turns),
        )

    async def _append(self, stream: str, event: Any) -> None:
        await self._client.append_to_stream(
            stream_name=stream,
            current_version=StreamState.ANY,
            events=[serialization.serialize(event)],
        )


_LLM_JUDGE_PROMPT = """\
You are an AI evaluator. Score the following agent response on a scale of 0.0 to 1.0.

Criteria: {criteria}

User input: {user_input}
{tool_context}
Agent output: {assistant_output}

Respond with ONLY a JSON object:
{{"score": <0.0-1.0>, "label": "<good|acceptable|poor>", "reason": "<brief explanation>"}}
"""


def llm_judge(
    chat_client: SupportsChatGetResponse,
    criteria: str,
    *,
    model: str | None = None,
) -> Scorer:
    """Build the canonical LLM-as-judge :data:`Scorer`.

    The scorer prompts ``chat_client`` for a JSON ``{score, label, reason}``
    object. Falls back to ``score=0.5`` / ``label="parse_error"`` when the
    model returns malformed JSON or an unexpected shape, mirroring
    ``EvalRunner.LlmJudge`` (.NET).
    """
    options: dict[str, Any] | None = {"model": model} if model else None

    async def _score(turn: Turn) -> ScoredTurn:
        # Normalise ``None`` to empty strings so the judge prompt never carries
        # the literal ``"None"`` token when ``read_session_turns`` produces an
        # incomplete turn (e.g. user message with no assistant reply yet).
        if turn.tool_calls:
            tool_context = "\nTool calls made:\n" + "\n".join(
                f"  - {tc.name}({tc.arguments or ''}) → {tc.result or ''}"
                for tc in turn.tool_calls
            )
        else:
            tool_context = ""

        prompt = _LLM_JUDGE_PROMPT.format(
            criteria=criteria,
            user_input=turn.user_input or "",
            tool_context=tool_context,
            assistant_output=turn.assistant_output or "",
        )

        response = cast(
            ChatResponse,
            await chat_client.get_response(
                [Message("user", [prompt])],
                options=options,
            ),
        )
        text = (response.text or "").strip()

        try:
            parsed = json.loads(text)
            score = float(parsed["score"])
            label = parsed.get("label")
            reason = parsed.get("reason")
            return ScoredTurn(turn=turn, score=score, label=label, reason=reason)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ScoredTurn(
                turn=turn,
                score=0.5,
                label="parse_error",
                reason=f"Could not parse judge response: {text}",
            )

    return _score
