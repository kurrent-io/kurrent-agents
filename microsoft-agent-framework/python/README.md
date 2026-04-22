# Kurrent.AgentFramework.Python

Event-sourced persistence for the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework), backed by [KurrentDB](https://www.kurrent.io). A conversation run through a MAF `Agent` becomes a stream of rich typed events you can query, project, and evaluate — not a blob of JSON in a table.

Events follow the canonical **schema v2** defined in [`schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md). Records, stream-name builders, the event-type registry, and the `$usage` metadata model come from the shared [`kurrent-agent-schema`](../../schema/python/) package, so every KurrentDB agent integration — including the [MAF .NET mirror](../dotnet/) — writes byte-identical JSON for the same logical content. Parity is continuously drift-tested against [`schema/fixtures/`](../../schema/fixtures/) on both sides.

MAF-specific payload fields go under the `afw` extension slug (see `SCHEMA_v2.md §5.2`).

## What it gives you

- **`KurrentDBHistoryProvider`** — a MAF `HistoryProvider` that decomposes each `Message` into typed canonical events (`UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived`) on save, and reconstructs them on read. `SessionStarted` / `SessionEnded` frame each conversation stream.
- **`KurrentDBAgentMemory`** + **`AgentMemoryContextProvider`** — fact recall and retention as `FactRetained` events in an `AgentMemory-{app}-{user}` stream, injected into each run as untrusted context.
- **`FactExtractionService`** / **`run_fact_extraction`** — background persistent subscription over every `AgentSession-*` stream that feeds each `UserMessageReceived` to a pluggable `FactExtractor` and retains the result via `AgentMemory`.

## Dependency gotcha (DEV-1495)

Depend on **`agent-framework-core`**, **not** the `agent-framework` meta-package. The meta pulls in `agent-framework-azure-ai-search==0.0.0a1`, a placeholder whose 0-byte `agent_framework/__init__.py` clobbers the real re-exports and breaks every top-level import (`Content`, `Message`, `HistoryProvider`, …). This package's `pyproject.toml` already pins the core package directly.

## Install (from source)

```bash
pip install -e ".[dev]"
```

The `kurrent-agent-schema` dep is pinned to the local path source at `schema/python/` via `tool.uv.sources`, so editable installs pick up shared-schema changes without republishing.

## Usage

```python
from agent_framework import Agent
from kurrentdbclient import AsyncKurrentDBClient
from kurrent_agent_framework import KurrentDBHistoryProvider

client = AsyncKurrentDBClient("kurrentdb://localhost:2113?Tls=false")
history = KurrentDBHistoryProvider(
    client,
    source_id="kurrentdb_history",
    app_name="my-app",     # recorded on SessionStarted (schema v2)
    agent_name="root",
    model_name="gpt-4o",
)

agent = Agent(
    chat_client=...,
    context_providers=[history],
)
```

### Background fact extraction

`FactExtractionService` subscribes to **every** `AgentSession-*` stream and feeds each `UserMessageReceived` to the extractor you pass in. Decide the memory scope explicitly — the default `KurrentDBAgentMemory()` stream (`AgentMemory`) is shared across everything in the process, so in a multi-tenant deployment you almost certainly want a per-tenant/user stream:

```python
from kurrent_agent_schema import agent_memory_stream
from kurrent_agent_framework import (
    KurrentDBAgentMemory, run_fact_extraction,
)

# Scope memory per app + user — NOT the default global "AgentMemory" stream.
memory = KurrentDBAgentMemory(
    client,
    stream_name=agent_memory_stream("my-app", user_id),
)

def my_extractor(message: str):
    # bring your own domain logic (regex, LLM, rules…)
    if "my name is" in message.lower():
        yield f"User: {message}"

async with run_fact_extraction(client, memory, my_extractor):
    # run your agent here; facts are extracted in the background
    ...
```

If you need per-session scope, or finer-grained per-session-extracting-into-per-tenant-memory routing, provide your own implementation of the `AgentMemory` protocol instead of using `KurrentDBAgentMemory` directly — the subscription itself is a single server-side group per process, so all session traffic flows through the one extractor you register.

See `samples/fact_extraction.py` for a runnable personal-assistant demo.

## Event model

Every agent interaction is stored as typed canonical events (from `kurrent_agent_schema`) in an `AgentSession-{id}` stream:

```
[0] SessionStarted              { app_name, agent_name, model, tenant_id, user_id, agent_config, previous_session_id, timestamp }
    metadata: { $schema_version: 2 }
[1] UserMessageReceived          { content, message_id, author_name, message_index, timestamp }
[2] AssistantToolCallsGenerated  { tool_calls: [{call_id, tool_name, arguments}] }
[3] ToolResultReceived           { call_id, tool_name, result, message_index, timestamp }
[4] AssistantTextGenerated       { content, message_id, author_name, message_index, timestamp }
[5] SessionEnded                 { reason, timestamp }
```

Every canonical event accepts an `extensions: { <slug>: {...} }` block. MAF Python writes framework-specific fields under the `afw` slug; readers from other integrations pass it through untouched. The full canonical vocabulary — `AssistantThinkingGenerated`, `SessionContinuedAs`, `SubagentStarted` / `SubagentCompleted`, `InterruptIssued` / `InterruptResolved`, `ArtifactVersionCreated`, and the eval events — lives in `kurrent_agent_schema.events`; add emit paths as needed.

The serialiser stamps `$schema_version = 2` on every event's KurrentDB metadata per `SCHEMA_v2.md §9`. Caller-supplied metadata is preserved; the schema version is stamped last so callers cannot forge a different wire version.

## Stream naming

Stream names are built with the shared `kurrent_agent_schema` helpers — never concatenate prefixes by hand.

| Stream | Builder | Purpose |
|---|---|---|
| `AgentSession-{id}` | `agent_session_stream(id)` | Conversation events for one session |
| `AgentSubsession-{parent}-{agent_id}` | `agent_subsession_stream(parent, agent_id)` | Subagent conversation stream (schema v2) |
| `AgentMemory-{app}-{user}` | `agent_memory_stream(app, user)` | Retained facts, per app + user |
| `EvalRun-{id}` | `eval_run_stream(id)` | Eval scores for a session |

## Run tests

```bash
pytest
```

The test suite includes:

- Unit tests for the serialiser adapter and memory provider (no server required).
- `test_fixtures_round_trip.py` — drift-detection against every fixture in `schema/fixtures/events/`, both via the pure codec and through an actual KurrentDB container (Testcontainers). Pairs with the MAF .NET `FixtureRoundTripTests` so the two integrations remain byte-identical.
- Integration tests for `FactExtractionService` against a Testcontainers KurrentDB.

## Parity with MAF .NET

The sister integration under [`microsoft-agent-framework/dotnet/`](../dotnet/) implements the same write + read surface in C#. Both consume the shared canonical types, round-trip against the same fixtures, and stamp `$schema_version = 2`. A stream produced by MAF Python can be read by MAF .NET (and vice versa) with no glue.
