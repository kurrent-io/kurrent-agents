# Eval UI: Kapacitor Integration Instead of OTEL Export

## Problem

The agent framework currently sends observability data via OTEL to external tools (Langfuse, Jaeger). This breaks the "events is all you need" promise — KurrentDB has all the data, but you still need a third-party tool to visualize it.

Langfuse cannot be adapted to use KurrentDB. Its storage layer is deeply coupled to ClickHouse (raw SQL in ~24 repository files, ClickHouse-specific query patterns, mutable upsert semantics). ClickHouse acquired Langfuse in Jan 2026, so this coupling will only deepen. Forking it would mean rewriting the entire analytical backend.

## Observation

kapacitor-server already has most of what an eval UI needs:

- Rich Blazor Server UI with session list, detail panel (Chat/Events/Trace/Details tabs)
- Hierarchical trace view with timing and token usage
- Event timeline with per-event type renderers
- Stats overlay (tokens by model, file changes, tool usage)
- Real-time updates via SignalR
- Shared Razor Class Library (340+ files) reusable across Blazor Server and MAUI
- KurrentDB subscription infrastructure (`$all` with regex stream filters)
- SQLite read model projections

What kapacitor-server is missing:

- Eval scoring display (pass/fail, rubrics, reasons)
- Analytics dashboard (the tab is a placeholder today)
- Understanding of agent-fw event streams (`AgentSession-*`, `EvalRun-*`)

## Proposal: Shared Event Contract + Kapacitor Projectors

### Architecture

```
agent-fw-experiment                    kapacitor-server
+-----------------------+              +----------------------------+
| Agents write events   |              | $all subscription          |
| to KurrentDB:         |              |   +-- Session-*  (claude)  |
|  AgentSession-*       |--KurrentDB-->|   +-- AgentSession-* (new) |
|  EvalRun-*            |              |   +-- EvalRun-*      (new) |
+-----------------------+              |                            |
                                       | New projectors:            |
+-----------------------+              |  AgentSessionProjector     |
| Kurrent.AgentFw      |              |  EvalRunProjector          |
|  .Events (NuGet)     |<--ref--------|                            |
|  - SessionStarted    |              | New UI:                    |
|  - TurnScored        |              |  EvalDashboard.razor       |
|  - EvalRunCompleted  |              |  ScoreTimeline.razor       |
|  - etc.              |              |  AnalyticsTab (filled in)  |
+-----------------------+              +----------------------------+
```

### Steps

1. **Publish event types as a NuGet package.** Extract `src/Kurrent.AgentFramework/Events/` into `Kurrent.AgentFramework.Events` (or include in the main package when it gets published). These are already clean sealed records with snake_case JSON serialization.

2. **Add agent session projector to kapacitor.** A new `AgentSessionProjector` that handles `AgentSession-*` stream events (`SessionStarted`, `UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived`, `SessionEnded`). Projects into the existing SQLite read model with session summaries and stats.

3. **Add eval run projector to kapacitor.** A new `EvalRunProjector` that handles `EvalRun-*` stream events (`EvalRunStarted`, `TurnScored`, `EvalRunCompleted`). New SQLite tables for eval runs, scored turns, and aggregate metrics.

4. **Build eval UI in kapacitor's Shared RCL.** A "Scores" tab on the session detail panel showing per-turn scores with labels and reasons. Fill in the Analytics tab with score trends, pass/fail rates, cost tracking, and comparisons across sessions/scorers.

5. **Make OtelGenAiProjection opt-in.** Keep it for users who want to export to Jaeger/Grafana, but remove it from the core "getting started" path. The default story becomes: events in KurrentDB, visualized in kapacitor.

### Why This Works

- **Closes the OTEL gap.** Kapacitor reads directly from KurrentDB. No external tool needed.
- **Minimal extraction.** Only the event types need to be shared as a package. No UI components or projection infrastructure to extract.
- **Kapacitor gets eval for free.** The UI infrastructure, real-time updates, and session viewing are already production-quality.
- **Agent framework stays focused.** It remains a library that writes events and provides scoring logic. It doesn't need to own visualization.
- **Both repos keep their own projection/read-model patterns.** No need to reconcile Eventuous vs raw KurrentDB client approaches.

### Key Differences to Bridge

| Aspect | kapacitor-server | agent-fw-experiment |
|--------|-----------------|---------------------|
| Event sourcing | Eventuous `[EventType]` | `EventTypeMap` + raw serialization |
| Stream naming | `Session-{id}`, `Agent-{id}` | `AgentSession-{id}`, `EvalRun-{id}` |
| Projections | SQLite via Eventuous subscriptions | In-process OTEL emission |
| Serialization | Eventuous built-in | Snake_case `EventSerializer` |

The new projectors in kapacitor would need to deserialize agent-fw events (snake_case JSON) rather than relying on Eventuous type mapping. This is straightforward — just register the agent-fw event types and add a custom deserializer or use the shared event package directly.
