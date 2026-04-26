"""Hybrid eval demo: heuristic first, LLM judge only on ambiguity.

Mirrors ``microsoft-agent-framework/dotnet/samples/HybridEvalDemo``.

* Cheap, confident signals (empty answer, clearly-correct answer with tool
  use, tool errors) are decided by the heuristic alone.
* Borderline cases (short answers, hedging, plausible-but-uncertain) are
  escalated to :func:`llm_judge`.

The runner only sees a single :data:`Scorer`; the routing logic lives inside
the user's scorer function, not in the runner.

Prereqs:

* a KurrentDB instance at ``kurrentdb://localhost:2113?Tls=false``
* ``pip install agent-framework-openai``  (or any other provider package
  exposing a :class:`SupportsChatGetResponse` chat client — swap ``OpenAIChatClient``
  below for the one you want)
* an ``OPENAI_API_KEY`` env var. Override the model with
  ``OPENAI_JUDGE_MODEL`` (default ``gpt-4o-mini``).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_framework import SupportsChatGetResponse
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    SessionEnded,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    agent_session_stream,
)
from kurrentdbclient import AsyncKurrentDBClient, StreamState

from kurrent_agent_framework import (
    EvalRunner,
    ScoredTurn,
    Scorer,
    Turn,
    llm_judge,
    serialization,
)


@dataclass
class ScorerStats:
    heuristic_only: int = 0
    escalated: int = 0


CRITERIA = "Response is helpful, factually correct, and uses tools when appropriate."


async def main() -> None:
    judge_client = _build_openai_client()

    session_id = str(uuid.uuid4())
    stream = agent_session_stream(session_id)
    now = datetime.now(UTC)

    client = AsyncKurrentDBClient("kurrentdb://localhost:2113?Tls=false")
    await client.connect()

    try:
        # --- Step 1: synthetic session covering confident + ambiguous turns ---
        print("=" * 40)
        print("Creating synthetic agent session")
        print(f"Stream: {stream}")
        print("=" * 40, "\n")

        events = [
            SessionStarted(agent_name="HybridEvalAgent", model="test-model", timestamp=now),
            # Turn 0 — confident pass: long answer + correct tool call.
            UserMessageReceived(content="What's the weather in London?", message_index=0, timestamp=now),
            AssistantToolCallsGenerated(
                tool_calls=[ToolCallInfo(call_id="call-1", tool_name="GetWeather")],
                content=None,
                message_index=1,
                timestamp=now,
            ),
            ToolResultReceived(
                call_id="call-1", tool_name="GetWeather", result="Sunny, 22°C", message_index=2, timestamp=now
            ),
            AssistantTextGenerated(
                content="The weather in London is sunny at around 22°C right now.",
                message_index=3,
                timestamp=now,
            ),
            # Turn 1 — confident fail: empty response.
            UserMessageReceived(content="Tell me a joke", message_index=4, timestamp=now),
            AssistantTextGenerated(content="", message_index=5, timestamp=now),
            # Turn 2 — ambiguous: short answer that may or may not be acceptable.
            UserMessageReceived(content="Is Paris the capital of France?", message_index=6, timestamp=now),
            AssistantTextGenerated(content="Yes.", message_index=7, timestamp=now),
            # Turn 3 — ambiguous: medium-length answer that hedges instead of using a tool.
            UserMessageReceived(content="What time is it in Tokyo?", message_index=8, timestamp=now),
            AssistantTextGenerated(
                content=(
                    "Tokyo is in JST, which is UTC+9, so you can work it out from your local time."
                ),
                message_index=9,
                timestamp=now,
            ),
            # Turn 4 — ambiguous: plausible-sounding but factually wrong.
            UserMessageReceived(content="Who wrote Hamlet?", message_index=10, timestamp=now),
            AssistantTextGenerated(
                content="Hamlet was written by Christopher Marlowe in the late 1500s.",
                message_index=11,
                timestamp=now,
            ),
            SessionEnded(reason="completed", timestamp=now),
        ]

        await client.append_to_stream(
            stream_name=stream,
            current_version=StreamState.ANY,
            events=[serialization.serialize(e) for e in events],
        )
        print(f"  Written {len(events)} events\n")

        # --- Step 2: build the hybrid scorer ---
        stats = ScorerStats()
        scorer = build_hybrid_scorer(judge_client, stats)

        # --- Step 3: run the eval ---
        print("=" * 40)
        print("Running hybrid eval (heuristic + LLM judge)")
        print("=" * 40, "\n")

        result = await EvalRunner(client).run(
            session_id=session_id,
            scorer_name="hybrid-v1",
            criteria=CRITERIA,
            scorer=scorer,
        )

        for scored in result.scored_turns:
            print(f"  Turn {scored.turn.index}: {scored.score:.2f} [{scored.label}]")
            print(f"    Input:  {scored.turn.user_input}")
            print(f"    Output: {scored.turn.assistant_output or '(empty)'}")
            if scored.reason:
                print(f"    Reason: {scored.reason}")
            print()

        print(f"  Average score:    {result.average_score:.2f}")
        print(f"  Heuristic-only:   {stats.heuristic_only} turn(s)")
        print(f"  Escalated to LLM: {stats.escalated} turn(s)")

        # --- Step 4: dump the EvalRun stream ---
        print("\n" + "=" * 40)
        print("Eval events in KurrentDB")
        print("=" * 40, "\n")

        recent = await client.read_all(
            backwards=True,
            limit=200,
            filter_include=["EvalRun-.+"],
            filter_by_stream_name=True,
        )
        eval_stream: str | None = None
        async for resolved in recent:
            eval_stream = resolved.stream_name
            break
        if eval_stream is not None:
            print(f"--- {eval_stream} ---\n")
            response = await client.read_stream(eval_stream)
            async for evt in response:
                data = evt.data.decode("utf-8")
                if len(data) > 200:
                    data = data[:200] + "..."
                print(f"  [{evt.stream_position}] {evt.type}")
                print(f"       {data}")

    finally:
        await client.close()


def build_hybrid_scorer(judge_client: SupportsChatGetResponse, stats: ScorerStats) -> Scorer:
    """Build a :data:`Scorer` that escalates ambiguous turns to ``llm_judge``."""
    judge = llm_judge(judge_client, CRITERIA, model=os.environ.get("OPENAI_JUDGE_MODEL"))

    async def hybrid(turn: Turn) -> ScoredTurn:
        heuristic = await demo_heuristic_scorer(turn)

        # Confident bands → trust the heuristic, skip the LLM.
        if heuristic.score >= 0.85 or heuristic.score <= 0.15:
            stats.heuristic_only += 1
            reason = f"[heuristic] {heuristic.reason}" if heuristic.reason else "[heuristic]"
            return ScoredTurn(
                turn=heuristic.turn,
                score=heuristic.score,
                label=heuristic.label,
                reason=reason,
            )

        # Ambiguous → escalate.
        stats.escalated += 1
        llm = await judge(turn)
        reason = f"[llm | heuristic={heuristic.score:.2f}] {llm.reason or ''}".rstrip()
        return ScoredTurn(turn=llm.turn, score=llm.score, label=llm.label, reason=reason)

    return hybrid


# --- Heuristic scorer (deterministic, no LLM) ---
# Returns extreme scores when confident, mid-band scores when uncertain — the
# hybrid wrapper uses the score band to decide whether to escalate.
async def demo_heuristic_scorer(turn: Turn) -> ScoredTurn:
    if not (turn.assistant_output or "").strip():
        return ScoredTurn(turn=turn, score=0.0, label="poor", reason="empty response")

    score = 1.0
    reasons: list[str] = []
    length = len(turn.assistant_output or "")
    if length < 10:
        score = 0.5
        reasons.append("very short response")
    elif length < 40:
        score = 0.6
        reasons.append("short response")

    error_tools = sum(1 for tc in turn.tool_calls if tc.is_error)
    if error_tools:
        score -= 0.2 * error_tools
        reasons.append(f"{error_tools} tool error(s)")

    user_input = (turn.user_input or "").lower()
    needs_tool = "weather" in user_input or "time" in user_input
    if needs_tool and not turn.tool_calls:
        score = min(score, 0.55)
        reasons.append("expected tool call but none made")

    score = max(0.0, min(1.0, score))
    label = "good" if score >= 0.8 else "acceptable" if score >= 0.5 else "poor"

    return ScoredTurn(turn=turn, score=score, label=label, reason="; ".join(reasons))


def _build_openai_client() -> SupportsChatGetResponse:
    """Lazy-import an OpenAI chat client so the module imports cleanly even
    when ``agent-framework-openai`` is not installed."""
    try:
        from agent_framework_openai import OpenAIChatClient  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - sample-only path
        raise SystemExit(
            "This sample needs `pip install agent-framework-openai`. "
            "Swap _build_openai_client() for any other SupportsChatGetResponse "
            "(Azure OpenAI, Anthropic, …) if you prefer."
        ) from exc

    if "OPENAI_API_KEY" not in os.environ:  # pragma: no cover - sample-only path
        raise SystemExit("Set OPENAI_API_KEY before running this sample.")

    model = os.environ.get("OPENAI_JUDGE_MODEL", "gpt-4o-mini")
    return OpenAIChatClient(model_id=model)


if __name__ == "__main__":
    asyncio.run(main())
