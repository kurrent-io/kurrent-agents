# OpenAI Agents BasicAgent sample

End-to-end demo of `KurrentDBSession` for the OpenAI Agents SDK. Conversation history persisted to KurrentDB; two turns sharing one `session_id` across fresh `Runner` + `Session` instances:

- Turn 1: "What's the weather in Tokyo?" — the agent calls the `get_weather` tool.
- Turn 2 (new Session, same session_id): "What temperature did you just tell me?" — persisted history lets the agent answer without a second tool call.

Third session then reads everything back from KurrentDB.

Mirrors `google-adk/python/samples/basic_agent/` and `strands/python/samples/basic_agent/` so the three integrations compare side by side.

## Model routing

By default the sample uses **Anthropic Claude via LiteLLM** (`agents.extensions.models.litellm_model.LitellmModel`) so it works with the monorepo's existing `ANTHROPIC_API_KEY`. To use a native OpenAI model, set `OPENAI_API_KEY` and change the `model=` line in `agent.py` to a bare model id string, e.g. `model="gpt-4o-mini"`.

## Prerequisites

```bash
docker compose up -d
export ANTHROPIC_API_KEY=sk-ant-...      # or OPENAI_API_KEY
pip install -e '.[dev]' 'openai-agents[litellm]'
```

## Run

From `openai-agents/python/`:

```bash
python -m samples.basic_agent.main
```

## What this demonstrates

- **Drop-in persistence.** `KurrentDBSession` is wired via `Runner.run(..., session=...)` — zero changes to the agent definition.
- **Cross-Session resume.** The second Runner's `Session` instance has no in-memory state; the SDK calls `session.get_items()` on the way in and receives the full history from KurrentDB.
- **Canonical decomposition under the hood.** Each Responses API item (message / function_call / function_call_output) becomes a canonical event (`UserMessageReceived`, `AssistantToolCallsGenerated`, `ToolResultReceived`, `AssistantTextGenerated`) — wire-compatible with the other framework integrations in the monorepo.
- **Lossless round-trip.** Every emitted event stashes the raw dict under `extensions.openai.raw_item`, so `get_items` returns exactly what `add_items` received.
