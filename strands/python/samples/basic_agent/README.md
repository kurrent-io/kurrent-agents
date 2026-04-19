# Strands BasicAgent sample

End-to-end demo of `KurrentDBSessionManager`: a Strands `Agent` backed by Anthropic Claude with conversation history persisted to KurrentDB.

Two turns across two fresh `Agent` + `KurrentDBSessionManager` instances sharing one `session_id`:

- Turn 1: "What's the weather in Tokyo?" — the agent calls the `get_weather` tool.
- Turn 2 (new Agent, new SessionManager, same session_id): "What temperature did you just tell me?" — the persisted history lets the agent answer without a second tool call.

A third `SessionManager` then reads the full message log from KurrentDB to show what actually landed, one line per message (including token usage pulled from `$usage` event metadata).

Mirrors the shape of `google-adk/python/samples/basic_agent/` so the two integrations compare side by side.

## Prerequisites

```bash
docker compose up -d
export ANTHROPIC_API_KEY=sk-ant-...
pip install -e '.[dev]'
```

## Run

From `strands/python/`:

```bash
python -m samples.basic_agent.main
```

## What this demonstrates

- **`KurrentDBSessionManager` wires up end-to-end with a real Strands `Agent`.**
- **Cross-Agent resume**: the second Agent has zero in-memory state; `SessionManager.initialize` reads every canonical event from the stream and reassembles `agent.messages`.
- **Canonical decomposition under the hood**: each Strands `Message` becomes one or more canonical events (`UserMessageReceived`, `AssistantToolCallsGenerated`, `ToolResultReceived`, `AssistantTextGenerated`), wire-compatible with the other framework integrations in the monorepo.
- **Token usage**: if Claude returns `Usage`, it lands as `$usage` KurrentDB event metadata (snake_case; see `DESIGN.md` §4).
