"""Lightweight eval tool demo — heuristic scorer, no LLM required.

Mirrors ``microsoft-agent-framework/dotnet/samples/EvalDemo``:

1. seeds a synthetic ``AgentSession-*`` stream with known turns,
2. replays the session into :class:`Turn` s via :func:`read_session_turns`,
3. scores each turn with a domain-specific heuristic and writes the results
   to a fresh ``EvalRun-*`` stream via :class:`EvalRunner`,
4. dumps the resulting eval events.

Swap the heuristic for :func:`llm_judge` (see ``hybrid_eval_demo.py``) if you
want LLM-as-judge scoring instead.

Prereq: a KurrentDB instance at ``kurrentdb://localhost:2113?Tls=false``
(use the docker-compose.yml in the C# repo, or: ``docker run -p 2113:2113
docker.kurrent.io/kurrent-latest/kurrentdb:latest --insecure --run-projections=All``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

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
    Turn,
    read_session_turns,
    serialization,
)


async def main() -> None:
    session_id = str(uuid.uuid4())
    stream = agent_session_stream(session_id)
    now = datetime.now(UTC)

    client = AsyncKurrentDBClient("kurrentdb://localhost:2113?Tls=false")
    await client.connect()

    try:
        # --- Step 1: synthetic session with known turns ---
        print("=" * 40)
        print("Creating synthetic agent session")
        print(f"Stream: {stream}")
        print("=" * 40, "\n")

        events = [
            SessionStarted(agent_name="EvalTestAgent", model="test-model", timestamp=now),
            # Turn 0 — good response
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
                content="The weather in London is sunny at 22°C.", message_index=3, timestamp=now
            ),
            # Turn 1 — poor response (empty)
            UserMessageReceived(content="Tell me a joke", message_index=4, timestamp=now),
            AssistantTextGenerated(content="", message_index=5, timestamp=now),
            # Turn 2 — acceptable but missed tool usage
            UserMessageReceived(content="What time is it in Tokyo?", message_index=6, timestamp=now),
            AssistantTextGenerated(
                content="I'm not sure of the exact time right now.", message_index=7, timestamp=now
            ),
            # Turn 3 — good response with facts
            UserMessageReceived(content="What is my name?", message_index=8, timestamp=now),
            AssistantTextGenerated(
                content="Your name is Alexey and you work at Kurrent.", message_index=9, timestamp=now
            ),
            SessionEnded(reason="completed", timestamp=now),
        ]

        await client.append_to_stream(
            stream_name=stream,
            current_version=StreamState.ANY,
            events=[serialization.serialize(e) for e in events],
        )
        print(f"  Written {len(events)} events\n")

        # --- Step 2: replay the session into Turn objects ---
        print("=" * 40)
        print("Extracted turns")
        print("=" * 40, "\n")
        turns = await read_session_turns(client, session_id)
        for turn in turns:
            tools = ", ".join(tc.name for tc in turn.tool_calls) if turn.tool_calls else "none"
            print(f"  Turn {turn.index}:")
            print(f"    Input:  {turn.user_input}")
            print(f"    Output: {turn.assistant_output or '(empty)'}")
            print(f"    Tools:  {tools}")
            print()

        # --- Step 3: run the heuristic eval ---
        print("=" * 40)
        print("Running heuristic eval")
        print("=" * 40, "\n")

        result = await EvalRunner(client).run(
            session_id=session_id,
            scorer_name="heuristic-v1",
            criteria="Response quality: completeness, tool usage, helpfulness",
            scorer=demo_heuristic_scorer,
        )

        for scored in result.scored_turns:
            print(f"  Turn {scored.turn.index}: {scored.score:.1f} [{scored.label}]")
            print(f"    Input:  {scored.turn.user_input}")
            print(f"    Output: {scored.turn.assistant_output or '(empty)'}")
            if scored.reason:
                print(f"    Reason: {scored.reason}")
            print()

        print(f"  Average score: {result.average_score:.2f}")
        print(
            f"  Total tokens:  {result.total_input_tokens or 0} in / "
            f"{result.total_output_tokens or 0} out"
        )

        # --- Step 4: dump the EvalRun stream ---
        print("\n" + "=" * 40)
        print("Eval events in KurrentDB")
        print("=" * 40, "\n")

        recent = await client.read_all(
            backwards=True,
            limit=100,
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
                if len(data) > 150:
                    data = data[:150] + "..."
                print(f"  [{evt.stream_position}] {evt.type}")
                print(f"       {data}")

    finally:
        await client.close()


# --- Demo-specific heuristic scorer ---
# Tailored to the synthetic turns above. Real scorers should be built per-domain
# against the ``Scorer = Callable[[Turn], Awaitable[ScoredTurn]]`` contract.
async def demo_heuristic_scorer(turn: Turn) -> ScoredTurn:
    score = 1.0
    reasons: list[str] = []

    if not (turn.assistant_output or "").strip():
        score = 0.0
        reasons.append("empty response")

    if turn.assistant_output is not None and len(turn.assistant_output) < 10:
        score -= 0.3
        reasons.append("very short response")

    error_tools = sum(1 for tc in turn.tool_calls if tc.is_error)
    if error_tools:
        score -= 0.2 * error_tools
        reasons.append(f"{error_tools} tool error(s)")

    user_input = (turn.user_input or "").lower()
    needs_tool = "weather" in user_input or "time" in user_input
    if needs_tool and not turn.tool_calls:
        score -= 0.3
        reasons.append("expected tool call but none made")

    score = max(0.0, min(1.0, score))
    label = "good" if score >= 0.8 else "acceptable" if score >= 0.5 else "poor"

    return ScoredTurn(turn=turn, score=score, label=label, reason="; ".join(reasons))


if __name__ == "__main__":
    asyncio.run(main())
