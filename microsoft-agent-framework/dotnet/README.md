# Kurrent AgentFramework

KurrentDB integration for [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/) — turns every agent interaction into a durable, queryable event stream.

Instead of scattering agent data across separate systems for persistence, memory, observability, and evaluation, write it once to KurrentDB and derive everything else as projections.

## Why KurrentDB

Traditional agent frameworks treat message persistence, memory, observability, and evaluation as separate concerns — each with its own database, pipeline, and schema. The same data gets written to multiple places, in multiple formats, with multiple failure modes.

An AI agent's execution is fundamentally a **stream of decisions**: messages received, tools called, results returned, responses generated. Each decision already carries rich data — content, timing, token costs, causation chains. KurrentDB is purpose-built for this:

| Property | What it enables |
|---|---|
| **Immutable append-only log** | Every agent decision is permanently recorded. Compliance and audit without extra work |
| **Catch-up subscriptions** | Memory that builds itself reactively from the event stream. No separate ETL pipeline |
| **Server-side projections** | Derived views (observability dashboards, eval datasets) that can be rebuilt on demand |
| **Temporal queries** | "What did the agent know at step 7?" is just reading to a stream position |
| **Stream-per-entity** | Natural mapping to agent sessions, users, workflows |
| **Optimistic concurrency** | Safe concurrent agent execution without locks |

The result: **one write, multiple capabilities, zero data duplication.**

```
Agent Run → Rich Typed Events → KurrentDB Stream
                                    │
                                    ├── Chat history (read the stream)
                                    ├── Token usage (event metadata)
                                    ├── Memory (catch-up subscription → search index)
                                    ├── Observability (project from events)
                                    └── Evaluation (same events, different query)
```

## What It Does

| Capability | How it works |
|---|---|
| **Message persistence** | `KurrentDBChatHistoryProvider` decomposes `ChatMessage` objects into typed events and reconstructs them on read |
| **Token usage tracking** | `UsageCapture` IChatClient middleware captures `UsageDetails` and attaches it as `$usage` metadata on assistant message events |
| **Cross-session memory** | `IAgentMemory` abstraction with a default KurrentDB-backed implementation; pluggable for alternative backends |
| **Automatic fact extraction** | `FactExtractionService` background subscription watches conversation events and retains facts using a pluggable `FactExtractor` delegate |
| **Session lifecycle** | `SessionStarted` / `SessionEnded` events frame each conversation stream |
| **Workflow checkpointing** | `KurrentDBCheckpointStore` stores workflow state at each superstep boundary, resumable across process restarts |
| **Multi-agent coordination** | `KurrentDBGroupChatManager` persists group chat turns; `StreamCoordinator` enables cross-process task distribution with correlated results |
| **Evaluation** | `EvalRunner` reads session turns, scores with pluggable scorers (heuristic or LLM-as-judge), writes scores back as events in `EvalRun-{id}` streams |

## Getting Started

### Prerequisites

- .NET 10 SDK
- Docker (for KurrentDB)

### 1. Start KurrentDB

```bash
docker compose up -d
```

### 2. Install packages

```bash
dotnet add package KurrentDB.Client
dotnet add package Microsoft.Agents.AI
dotnet add package Microsoft.Agents.AI.Abstractions
dotnet add package Microsoft.Agents.AI.Workflows  # if using workflows
```

Add a project reference to `Kurrent.AgentFramework` (not yet published as a NuGet package).

### 3. Configure

```json
{
  "KurrentDb": {
    "ConnectionString": "kurrentdb://localhost:2113?tls=false"
  }
}
```

## Usage

### Basic: Chat history persistence

Register the KurrentDB client in your host:

```csharp
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddKurrentAgentFramework(builder.Configuration);
```

Create an agent with KurrentDB-backed chat history:

```csharp
var kurrentDb = host.Services.GetRequiredService<KurrentDBClient>();
var sessionId = Guid.NewGuid().ToString();

AIAgent agent = new ChatClientAgent(
    chatClient,
    new ChatClientAgentOptions {
        Name = "MyAgent",
        ChatOptions = new() { Instructions = "You are a helpful assistant." },
        ChatHistoryProvider = new KurrentDBChatHistoryProvider(
            kurrentDb, sessionId),
    }
);

var session = await agent.CreateSessionAsync();
await agent.RunAsync("Hello!", session);
```

Messages are decomposed into typed events (`UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived`) in an `AgentSession-{id}` stream. To resume a conversation, create a new agent with the same `sessionId` — history loads from KurrentDB.

### Adding token usage capture

Wrap the `IChatClient` with `UsageCapture` to attach token counts as event metadata on assistant messages:

```csharp
var usageCapture = new UsageCapture();

AIAgent agent = new ChatClientAgent(
    usageCapture.Wrap(chatClient),
    new ChatClientAgentOptions {
        ChatHistoryProvider = new KurrentDBChatHistoryProvider(
            kurrentDb, sessionId, usageCapture),
    }
);
```

Each assistant message event now carries `$usage` in its metadata:

```json
{
  "$usage": {
    "input_tokens": 31,
    "output_tokens": 20,
    "total_tokens": 51,
    "cached_input_tokens": 0
  }
}
```

No separate telemetry pipeline. Usage is correlated with the message that incurred it.

### Adding cross-session memory

Agent memory is an `IAgentMemory` with `RecallAsync` and `RetainAsync`. The default implementation stores facts as events in a KurrentDB stream — no external search index needed:

```csharp
builder.Services.AddKurrentAgentFramework(builder.Configuration);
builder.Services.AddKurrentAgentMemory();
```

Attach the memory provider to your agent:

```csharp
var memoryProvider = host.Services.GetRequiredService<AgentMemoryContextProvider>();

AIAgent agent = new ChatClientAgent(
    usageCapture.Wrap(chatClient),
    new ChatClientAgentOptions {
        ChatHistoryProvider = new KurrentDBChatHistoryProvider(
            kurrentDb, sessionId, usageCapture),
        AIContextProviders = [memoryProvider],
    }
);
```

Before each agent run, `AgentMemoryContextProvider` recalls facts via `IAgentMemory` and injects them as context. Facts can be retained explicitly (via a `RetainFact` tool) or automatically — pass a `FactExtractor` delegate:

```csharp
builder.Services.AddKurrentAgentMemory(message => {
    // Your domain-specific extraction logic here.
    // Return facts as plain strings.
});
```

The default `KurrentDBAgentMemory` returns *every* retained fact on recall — simple, adequate for small fact sets. Bring your own `IAgentMemory` implementation for other backends (Redis, Postgres, a dedicated vector store, etc.).

### Workflow checkpointing

For durable, resumable workflows, use `KurrentDBCheckpointManagerFactory`:

```csharp
var checkpointManager = KurrentDBCheckpointManagerFactory.Create(kurrentDb);

var workflow = new WorkflowBuilder(executorA)
    .AddEdge(executorA, executorB)
    .AddEdge(executorB, executorC)
    .WithOutputFrom(executorC)
    .Build();

// Run with checkpointing
var run = await InProcessExecution.RunStreamingAsync(
    workflow, input, checkpointManager);
```

Checkpoints are stored as events in `WorkflowCheckpoint-{sessionId}` streams. Resume from any superstep:

```csharp
// Later — even after process restart
var resumedRun = await InProcessExecution.ResumeStreamingAsync(
    newWorkflow, savedCheckpoint, checkpointManager);
```

### Multi-agent coordination

**Durable group chat** — use `KurrentDBGroupChatManager` to persist every agent turn as events:

```csharp
var groupChatManager = new KurrentDBGroupChatManager(
    client:     kurrentDb,
    chatId:     "my-chat-123",
    agents:     [analyst, reviewer, manager],
    selectNext: (history, agents, iteration) => agents[iteration % agents.Count],
    maxIterations: 10
);

Workflow workflow = AgentWorkflowBuilder
    .CreateGroupChatBuilderWith(_ => groupChatManager)
    .AddParticipants(analyst, reviewer, manager)
    .Build();
```

Each turn is an `AgentTurnTaken` event in a `GroupChat-{id}` stream. The full multi-agent conversation is auditable and replayable.

**Cross-process task distribution** — use `StreamCoordinator` for fire-and-forget or request/reply:

```csharp
var coordinator = new StreamCoordinator(kurrentDb);

// Publisher
await coordinator.PublishAsync("AgentTasks", "TaskAssigned", new { TaskId = "t-1", Description = "..." });

// Consumer (different process)
await coordinator.SubscribeAsync<TaskItem>("AgentTasks", async (task, eventType, ct) => {
    var result = await ProcessTask(task);
    await coordinator.PublishResultAsync("AgentResults", task.TaskId, result);
});
```

Results carry `$correlationId` in metadata linking back to the originating task.

### Evaluation

Run evals against recorded sessions — scores are written back as events:

```csharp
var evalRunner = new EvalRunner(kurrentDb);

// LLM-as-judge scorer
var result = await evalRunner.RunAsync(
    sessionId, "gpt-4o-judge", "helpfulness",
    EvalRunner.LlmJudge(chatClient, "Rate helpfulness 0-1"));

// Or bring your own scorer — any Func<Turn, CancellationToken, Task<ScoredTurn>>
var result = await evalRunner.RunAsync(
    sessionId, "my-scorer", "domain-specific criteria",
    async (turn, ct) => {
        var score = /* your scoring logic */ 0.9;
        return new ScoredTurn(turn, score, "good", "reason");
    });
```

Scores are events in `EvalRun-{id}` streams:

```
[0] EvalRunStarted    { session_id, scorer, criteria }
[1] TurnScored        { turn_index: 0, score: 1.0, label: "good" }
[2] TurnScored        { turn_index: 1, score: 0.0, label: "poor", reason: "empty response" }
[3] TurnScored        { turn_index: 2, score: 0.7, label: "acceptable", reason: "missed tool call" }
[4] EvalRunCompleted  { turns_scored: 3, average_score: 0.57 }
```

Same data surface — eval results are queryable, projectable, and versionable alongside the agent data they score.

### End a session

Write a `SessionEnded` event to close the stream:

```csharp
var (agent, historyProvider) = CreateAgent(sessionId);
// ... conversation ...
await historyProvider.EndSessionAsync("completed");
```

## Samples

### BasicAgent

A multi-turn conversational agent with Anthropic Claude that demonstrates the full integration:

1. **Session 1** — User introduces themselves ("My name is Alexey, I work at Kurrent, I prefer dark mode"). A `FactExtractor` delegate pulls personal facts from the user message and retains them via the default `KurrentDBAgentMemory`; the agent then answers a weather question using `GetWeather` tool. All messages, tool calls, and token usage are persisted as events.

2. **Session 2** — A completely new agent instance with no shared conversation history. `AgentMemoryContextProvider` recalls the retained facts and injects them as context. The agent correctly answers "What do you know about my preferences?" with all three facts.

Demonstrates: chat persistence, tool call capture, inline usage metadata, cross-session memory recall, session lifecycle events.

```bash
cd samples/BasicAgent
DOTNET_ENVIRONMENT=Development dotnet run
```

Requires an Anthropic API key under `Anthropic:ApiKey` in `appsettings.Development.json`:

```json
{ "Anthropic": { "ApiKey": "sk-ant-..." } }
```

### WorkflowWithCheckpoints

A 3-step document processing pipeline (Classify → Process → Review) with KurrentDB-backed checkpointing:

1. **Run 1** — Executes the full pipeline. At each superstep boundary, a checkpoint is saved as an event in KurrentDB. The output shows each executor completing and checkpoints being created.

2. **Run 2** — Creates a fresh workflow instance (simulating a process restart) and resumes from the first checkpoint (after Classify). Only Processor and Reviewer execute — Classifier is skipped. Same final result.

Demonstrates: durable workflow checkpoints, superstep-based state capture, resume from any point, parent checkpoint chain in event metadata.

```bash
cd samples/WorkflowWithCheckpoints
dotnet run
```

No API key required — uses deterministic executors (no LLM calls).

### MultiAgentCoordination

Two coordination patterns backed by KurrentDB streams:

1. **Task distribution** — `StreamCoordinator` publishes 3 work items to an `AgentTasks` stream. A simulated agent consumes them via catch-up subscription and publishes correlated results to an `AgentResults` stream. Each result carries `$correlationId` in metadata.

2. **Durable group chat** — Three agents (Analyst, Reviewer, Manager) take turns in a round-robin discussion. `KurrentDBGroupChatManager` records each turn as an `AgentTurnTaken` event and emits `GroupChatCompleted` on termination. The full conversation persists in a `GroupChat-{id}` stream.

Demonstrates: cross-process task distribution, correlated results, durable multi-agent conversations, auditable turn history.

```bash
cd samples/MultiAgentCoordination
dotnet run
```

No API key required — uses simulated agents.

### EvalDemo

Lightweight evaluation tool that scores agent sessions from KurrentDB:

1. **Creates a synthetic session** with 4 turns of varying quality — good response with tool call, empty response, missed tool call, correct memory recall.

2. **Reads turns** from the session stream using `SessionTurnReader` — groups events into input/output/tool-call triples.

3. **Runs heuristic eval** — `EvalRunner` scores each turn and writes `TurnScored` events to an `EvalRun-{id}` stream. Results: Turn 0 = 1.0 (good), Turn 1 = 0.0 (empty), Turn 2 = 0.7 (missed tool), Turn 3 = 1.0 (good).

Demonstrates: session-to-turn extraction, pluggable scoring (heuristic or LLM-as-judge), eval scores as events in KurrentDB.

```bash
cd samples/EvalDemo
dotnet run
```

Demonstrates session-to-turn extraction and pluggable scoring. The demo includes a sample heuristic scorer; swap in `EvalRunner.LlmJudge(chatClient, criteria)` or any custom `Func<Turn, CancellationToken, Task<ScoredTurn>>` for real use.

### HybridEvalDemo

Same scoring contract as EvalDemo, but composes a heuristic scorer with `EvalRunner.LlmJudge` so the LLM is only called on ambiguous turns:

1. **Synthetic session** with five turns spanning the confidence spectrum — clearly good (long answer + correct tool call), clearly bad (empty), and three ambiguous cases (terse "Yes." reply, plausible hedge instead of a tool call, plausible-sounding factual error).

2. **Heuristic-first scoring** — fast, deterministic checks return scores in extreme bands (≤0.15 or ≥0.85) when they are confident.

3. **LLM escalation** — turns landing in the mid-band are forwarded to `EvalRunner.LlmJudge`, which returns the final score. Each `TurnScored` event records which path was taken via the `reason` field (`[heuristic]` or `[llm | heuristic=…]`), and the run prints a per-source count.

Demonstrates: composing scorers behind the single `Func<Turn, CancellationToken, Task<ScoredTurn>>` contract, keeping LLM cost proportional to ambiguity rather than session size.

```bash
cd samples/HybridEvalDemo
DOTNET_ENVIRONMENT=Development dotnet run
```

Requires an Anthropic API key under `Anthropic:ApiKey` in `appsettings.Development.json` (only used for the escalated turns):

```json
{ "Anthropic": { "ApiKey": "sk-ant-..." } }
```

## Event Model

Every agent interaction is stored as typed events in an `AgentSession-{id}` stream:

```
[0] SessionStarted              { agent_name, model, timestamp }
[1] UserMessageReceived          { content, message_id, author_name }
[2] AssistantToolCallsGenerated  { tool_calls: [{call_id, tool_name, arguments}] }
    metadata: { $usage: { input_tokens: 1507, output_tokens: 203 } }
[3] ToolResultReceived           { call_id, result }
[4] AssistantTextGenerated       { content, message_id, author_name }
    metadata: { $usage: { input_tokens: 897, output_tokens: 44 } }
[5] SessionEnded                { reason, timestamp }
```

## Stream Naming

| Stream | Purpose |
|---|---|
| `AgentSession-{id}` | Conversation events for one session |
| `WorkflowCheckpoint-{id}` | Workflow state at each superstep boundary |
| `GroupChat-{id}` | Multi-agent group chat turn history |
| `EvalRun-{id}` | Eval scores for a session (TurnScored, EvalRunCompleted) |
| `AgentMemory` | Retained facts (default `KurrentDBAgentMemory`) |

## Dependencies

- [KurrentDB.Client](https://www.nuget.org/packages/KurrentDB.Client) 1.3.1
- [Microsoft.Agents.AI](https://www.nuget.org/packages/Microsoft.Agents.AI) 1.0.0
- [Microsoft.Agents.AI.Workflows](https://www.nuget.org/packages/Microsoft.Agents.AI.Workflows) 1.0.0
