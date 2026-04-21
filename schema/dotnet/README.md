# Kurrent.Agent.Schema

Canonical event schema types for Kurrent agent integrations — schema version **2**.

This package is the .NET mirror of the canonical agent event schema shared across Kurrent's agent-framework integrations (Google ADK, Microsoft Agent Framework, Strands, OpenAI Agents, Claude Agent SDK) and Capacitor. The prose specification lives in [`schema/SCHEMA_v2.md`](../SCHEMA_v2.md); the Python mirror is [`kurrent-agent-schema`](../python/).

## What's here

- **Canonical event records** (`Kurrent.Agent.Schema.Events`): `SessionStarted`, `SessionEnded`, `SessionContinuedAs`, `UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `AssistantThinkingGenerated`, `ToolResultReceived`, `InterruptIssued`, `InterruptResolved`, `SubagentStarted`, `SubagentCompleted`, `FactRetained`, `ArtifactVersionCreated`, `EvalRunStarted`, `TurnScored`, `EvalRunCompleted`.
- **Value types**: `AgentConfig`, `ToolSpec`, `ToolCallInfo`.
- **Usage metadata**: `TokenUsage` + `UsageMetadata.Key` (the `$usage` KurrentDB metadata key).
- **Stream-name builders**: `StreamNames.AgentSession`, `AgentSubsession`, `AgentMemory`, `AgentArtifact`, `EvalRun`.
- **JSON options**: `SchemaJsonOptions.Default` — snake_case policy, skip-unknown on read, null-skip on write, `Z`-suffix for UTC datetimes.
- **Event-type map**: `EventTypeMap.GetName(Type)` / `GetType(string)` / `All`.

## What's not here

Framework-specific events (ADK `AgentTransferred`, AFW `WorkflowCheckpoint`, Capacitor `AgentRunStarted` / visibility events) and `extensions.{slug}` shapes live in their owning integration packages. This package carries only the portable canonical vocabulary.

## Usage

```csharp
using System.Text.Json;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;

var evt = new UserMessageReceived(
    Content:      "hello",
    MessageId:    null,
    AuthorName:   null,
    CreatedAt:    null,
    MessageIndex: 0,
    Timestamp:    DateTimeOffset.UtcNow,
    Extensions:   null
);

var json = JsonSerializer.Serialize(evt, SchemaJsonOptions.Default);
var stream = StreamNames.AgentSession("sess-0001"); // "AgentSession-sess-0001"
var eventTypeName = EventTypeMap.GetName(typeof(UserMessageReceived)); // "UserMessageReceived"
```

## Drift guard

Every canonical event has a JSON fixture under [`schema/fixtures/events/`](../fixtures/events/). The test project (`Kurrent.Agent.Schema.Tests`) runs a round-trip assertion per fixture; an equivalent suite in the Python package runs against the same fixtures. Adding or modifying a canonical field requires a coordinated PR: update both packages and the fixture.

```bash
cd schema/dotnet
dotnet test
```

## Version

- Package: `0.1.0` (initial pre-1.0 release carrying schema v2).
- Schema: `SchemaVersion.Current = 2`, stamped on KurrentDB metadata under `$schema_version` by integration writers.
