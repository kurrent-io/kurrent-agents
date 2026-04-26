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
from kurrent_agent_schema import (
    AssistantTextGenerated,
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
from pydantic import ValidationError

from . import serialization

logger = logging.getLogger("kurrent_agent_framework.eval")


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation captured during a turn."""

    name: str
    arguments: str | None
    result: str | None
    is_error: bool


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

    current_input: str | None = None
    current_output: str | None = None
    current_tools: list[ToolCall] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    turn_index = 0

    try:
        response = await client.read_stream(stream)
        async for recorded in response:
            try:
                event = serialization.deserialize(recorded)
            except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as exc:
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
                if current_input is not None:
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
                    current_output = None
                    current_tools = []
                    input_tokens = None
                    output_tokens = None
                current_input = event.content
            elif isinstance(event, AssistantToolCallsGenerated):
                current_tools.extend(
                    ToolCall(
                        name=tc.tool_name,
                        arguments=_arguments_to_str(tc.arguments),
                        result=None,
                        is_error=False,
                    )
                    for tc in event.tool_calls
                )
                input_tokens, output_tokens = _accumulate_usage(
                    recorded, input_tokens, output_tokens
                )
            elif isinstance(event, ToolResultReceived):
                # Match the most recent unresolved tool call. Same back-walk
                # the .NET reader uses; preserves call order when several
                # tools are dispatched in one assistant turn.
                for i in range(len(current_tools) - 1, -1, -1):
                    if current_tools[i].result is None:
                        current_tools[i] = replace(current_tools[i], result=event.result)
                        break
            elif isinstance(event, AssistantTextGenerated):
                current_output = event.content
                input_tokens, output_tokens = _accumulate_usage(
                    recorded, input_tokens, output_tokens
                )
    except NotFoundError:
        return turns

    if current_input is not None:
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

    return turns


def _arguments_to_str(arguments: dict[str, Any] | None) -> str | None:
    """Render tool-call arguments as a compact JSON string for display."""
    if arguments is None:
        return None
    return json.dumps(arguments, separators=(",", ":"))


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

        average = (
            sum(s.score for s in scored_turns) / len(scored_turns)
            if scored_turns
            else 0.0
        )

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
        if turn.tool_calls:
            tool_context = "\nTool calls made:\n" + "\n".join(
                f"  - {tc.name}({tc.arguments}) → {tc.result}" for tc in turn.tool_calls
            )
        else:
            tool_context = ""

        prompt = _LLM_JUDGE_PROMPT.format(
            criteria=criteria,
            user_input=turn.user_input,
            tool_context=tool_context,
            assistant_output=turn.assistant_output,
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
