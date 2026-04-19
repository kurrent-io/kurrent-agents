# Research agent sample

Hard-mode sample exercising **every** KurrentDB-backed service in one flow:

- `KurrentDBSessionService` — conversation persistence inside each session.
- `KurrentDBMemoryService` — cross-session fact retention via `remember` / `load_memory`.
- `KurrentDBArtifactService` — versioned, user-scoped "notes" via `save_note` / `load_note` / `list_notes`.

## Flow

**Session A** — user builds up state:
1. Share notes on event sourcing → agent `save_note` + `remember`.
2. Share notes on KurrentDB → another `save_note` + `remember`.
3. Update event-sourcing notes → `save_note` again (creates version 1).
4. "What notes do I have?" → agent `list_notes`.

**Direct inspection** between sessions prints what landed in memory and in the artifact store.

**Session B** — new `session_id`, no shared conversation history:
5. "What have I been researching?" → agent `load_memory`, answers from retained facts.
6. "Show me the event-sourcing notes" → agent `load_note`, returns the latest (v1) content.

This demonstrates the three services together: session conversation is session-local, memory is user-scoped and crosses sessions, artifacts are user-scoped and also cross sessions.

## Prerequisites

- KurrentDB running: `docker compose up -d`
- Anthropic API key: `export ANTHROPIC_API_KEY=sk-ant-...`
- Package installed: `pip install -e '.[dev]' 'google-adk[extensions]'`

## Run

From `google-adk/python/`:

```bash
python -m samples.research_agent.main
```

## Notes on the code

- **User-scoped artifacts.** ADK's `tool_context.save_artifact(...)` always scopes to the current session id. To save user-scoped artifacts (visible across all the user's sessions), the sample calls the underlying service via `tool_context._invocation_context.artifact_service` — documented as a sample pattern, not a library API. A proper package-level helper is a future convenience.
- **Versioning.** `save_note` creates a new version each time it's called with the same name. The sample intentionally saves `event-sourcing` twice; the direct inspection shows `versions=[0, 1]`, and Session B's `load_note` returns version 1 (latest) by default.
- **Cross-session recall.** Session B's `Runner` has zero in-context history (different `session_id` → different `AgentSession-*` stream). The agent relies entirely on `load_memory` to know what the user was researching, and on `load_note` to read back the actual content.
