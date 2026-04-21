# kurrent-agent-schema

Canonical event schema types for Kurrent agent integrations — schema version **2**.

This package is the Python mirror of the canonical agent event schema shared across Kurrent's agent-framework integrations (Google ADK, Microsoft Agent Framework, Strands, OpenAI Agents, Claude Agent SDK) and Capacitor. The prose specification lives in [`schema/SCHEMA_v2.md`](../SCHEMA_v2.md); the .NET mirror is [`Kurrent.Agent.Schema`](../dotnet/Kurrent.Agent.Schema/).

## What's here

- **Canonical event models** (Pydantic v2): `SessionStarted`, `SessionEnded`, `SessionContinuedAs`, `UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `AssistantThinkingGenerated`, `ToolResultReceived`, `InterruptIssued`, `InterruptResolved`, `SubagentStarted`, `SubagentCompleted`, `FactRetained`, `ArtifactVersionCreated`, `EvalRunStarted`, `TurnScored`, `EvalRunCompleted`.
- **Value types**: `AgentConfig`, `ToolSpec`, `ToolCallInfo`.
- **Usage metadata**: `TokenUsage` (carried on KurrentDB event metadata under the `$usage` key, not as a standalone event).
- **Stream-name builders**: `agent_session_stream`, `agent_subsession_stream`, `agent_memory_stream`, `agent_artifact_stream`, `eval_run_stream`.
- **Type-name registry**: `EVENT_TYPE_NAMES` / `EVENT_TYPE_BY_NAME` for wiring up KurrentDB event-type to/from CLR-type conversion.

## What's not here

- **Framework-specific events and extension shapes.** ADK's `AgentTransferred`/`Rewind`, AFW's workflow checkpoints, Capacitor's `AgentRunStarted`/visibility events, coding-agent fields under `extensions.claude_code.*`, etc. Those live in their owning integration packages — this package deliberately carries only the portable vocabulary.

## Usage

```python
from datetime import datetime, timezone
from kurrent_agent_schema import (
    SessionStarted, UserMessageReceived, AssistantTextGenerated,
    TokenUsage, USAGE_METADATA_KEY, agent_session_stream,
)

stream = agent_session_stream("sess-0001")  # "AgentSession-sess-0001"

event = UserMessageReceived(
    content="hello",
    message_index=0,
    timestamp=datetime.now(tz=timezone.utc),
    extensions={"adk": {"invocation_id": "inv_abc"}},
)

# $usage goes on KurrentDB metadata, not the payload
usage = TokenUsage(input_tokens=1507, output_tokens=203, model="claude-sonnet-4-6")
```

## Drift guard

Every canonical event has a JSON fixture under [`schema/fixtures/events/`](../fixtures/events/). The test suite here runs a round-trip assertion per fixture; an equivalent suite in the .NET package runs against the same fixtures. Adding or modifying a canonical field requires a coordinated PR: update the model **and** the fixture, in both Python and .NET packages. CI fails otherwise.

```bash
cd schema/python
uv pip install -e ".[dev]"
pytest
```

## Version

- Package: `2.0.0` (initial v2 release).
- Schema: `SCHEMA_VERSION = 2`, stamped on KurrentDB metadata under `$schema_version` by integration writers.
