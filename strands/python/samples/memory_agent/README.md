# Strands memory_agent sample

Cross-session memory for a Strands agent backed by `KurrentDBAgentMemory`.

- Session A: user introduces themselves; agent calls `remember` once per fact → canonical `FactRetained` events land on `AgentMemory-{app}-{user}`.
- Session B: different `session_id`, same `user_id`. No shared chat history. Agent calls `recall_memory`, receives the facts, and answers.

Mirrors `google-adk/python/samples/memory_agent/` so the two integrations compare directly. Memory writes use the **same canonical stream and `FactRetained` event shape** as the ADK version — so a Strands agent's retained facts are visible to an ADK agent (and vice versa) for the same `(app, user)` scope.

## Prerequisites

```bash
docker compose up -d
export ANTHROPIC_API_KEY=sk-ant-...
pip install -e '.[dev]'
```

## Run

From `strands/python/`:

```bash
python -m samples.memory_agent.main
```

## Key differences from the ADK memory sample

- **No built-in `LoadMemoryTool`.** Strands doesn't ship a memory abstraction, so this sample exposes both the write and read sides as explicit tools (`remember` and `recall_memory`) closed over a single `KurrentDBAgentMemory` instance.
- **Sync.** Both the `KurrentDBAgentMemory` and the `SessionManager` use the sync `KurrentDBClient` because Strands' hook callbacks and tool functions are synchronous.
- **`query` ignored at the baseline.** `recall_memory` returns every retained fact; swap in a custom `KurrentDBAgentMemory` subclass for richer retrieval.
