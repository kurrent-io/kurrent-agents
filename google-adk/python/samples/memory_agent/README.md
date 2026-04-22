# Memory agent sample

Cross-session memory demo backed by `KurrentDBMemoryService`. An agent retains facts about the user in one session and recalls them in the next — different `session_id`s, same `(app_name, user_id)` scope.

## What it demonstrates

- **Writing to memory from a tool call.** A custom `remember(fact)` tool calls `tool_context.add_memory(...)`, which routes to `KurrentDBMemoryService.add_memory` and lands a canonical `FactRetained` event on `AgentMemory-{app_name}-{user_id}`.
- **Reading memory from a tool call.** ADK's built-in `load_memory_tool` exposes `load_memory(query)` to the LLM, which delegates to `BaseMemoryService.search_memory` — our implementation returns every retained entry (baseline; subclass `KurrentDBMemoryService` for richer retrieval).
- **Cross-session reachability.** Session A and Session B have different `session_id`s (and therefore different `AgentSession-*` streams), but the agent sees the facts from Session A in Session B because memory is scoped per `(app_name, user_id)`, not per session.

## Prerequisites

- KurrentDB running:

  ```bash
  docker compose up -d
  ```

- Anthropic API key:

  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...
  ```

- Package + ADK LiteLlm extension installed:

  ```bash
  pip install -e '.[dev]' 'google-adk[extensions]'
  ```

## Run

From `google-adk/python/`:

```bash
python -m samples.memory_agent.main
```

Expected output (abridged):

```
=== Session A (introductions) ===
User:  Hi! My name is Alexey, I work at Kurrent building an event-sourced agent platform, and I prefer dark mode IDEs.
       └── tool: remember({'fact': "User's name is Alexey"})
       └── tool: remember({'fact': 'User works at Kurrent building an event-sourced agent platform'})
       └── tool: remember({'fact': 'User prefers dark mode IDEs'})
Agent: Nice to meet you, Alexey! I'll remember that.

=== Retained facts (3 entries) ===
  - User prefers dark mode IDEs
  - User works at Kurrent building an event-sourced agent platform
  - User's name is Alexey

=== Session B (new session, same user) ===
User:  What do you know about me?
       └── tool: load_memory({'query': 'information about the user'})
Agent: Here's what I know: your name is Alexey, you work at Kurrent on an event-sourced agent platform, and you prefer dark mode IDEs.
```

## How it works

1. **Session A turn** — user introduces themselves. The LLM decides to call `remember` once per distinct fact; each call writes one canonical `FactRetained` event to KurrentDB.
2. **Direct memory inspection** — the sample calls `memory_service.search_memory` between sessions just to show what landed. In a real app the agent never sees this — only the LLM via `load_memory`.
3. **Session B turn** — a fresh session with no shared conversation history. The LLM has no in-context knowledge of the user, so it calls `load_memory`, receives the retained facts, and answers from them.

The two sessions live on entirely separate KurrentDB streams (`AgentSession-intro-...` and `AgentSession-followup-...`). Memory cross-links them via the shared `AgentMemory-{app}-{user}` stream.
