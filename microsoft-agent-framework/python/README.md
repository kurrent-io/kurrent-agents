# Kurrent.AgentFramework.Python

Python port of the Kurrent.AgentFramework — event-sourced persistence for the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework), backed by [KurrentDB](https://www.kurrent.io).

**Status: spike.** This package currently contains:

- Event models (Pydantic v2) matching the C# event schema exactly
- `KurrentDBHistoryProvider` — persists chat history as rich typed events
- `KurrentDBAgentMemory` / `AgentMemoryContextProvider` — fact recall + retention
- `FactExtractionService` / `run_fact_extraction` — background projection that extracts facts from user messages via a pluggable `FactExtractor`

The event wire format is shared with the C# implementation: snake_case JSON, same event type names, same stream naming conventions. A Python agent and a C# agent can read/write the same stream.

See [Kurrent.AgentFramework](https://github.com/kurrent-io/Kurrent.AgentFramework) for the full (C#) feature set — memory abstraction, fact extraction, eval runner, OTEL projection, workflows. These will be ported once the foundation is validated.

## Install (from source)

```bash
pip install -e ".[dev]"
```

## Usage

```python
from agent_framework import Agent
from kurrentdbclient import AsyncKurrentDBClient
from kurrent_agent_framework import KurrentDBHistoryProvider

client = AsyncKurrentDBClient("kurrentdb://localhost:2113?Tls=false")
history = KurrentDBHistoryProvider(client, source_id="kurrentdb_history")

agent = Agent(
    chat_client=...,
    context_providers=[history],
)
```

### Background fact extraction

`FactExtractionService` subscribes to **every** `AgentSession-*` stream and
feeds each `UserMessageReceived` to the extractor you pass in. Decide the
memory scope explicitly — the default `KurrentDBAgentMemory()` stream
(`AgentMemory`) is shared across everything in the process, so in a
multi-tenant deployment you almost certainly want a per-tenant/user stream:

```python
from kurrent_agent_framework import (
    KurrentDBAgentMemory, run_fact_extraction,
)

# Scope memory per user/tenant — NOT the default global "AgentMemory" stream.
memory = KurrentDBAgentMemory(client, stream_name=f"AgentMemory-{user_id}")

def my_extractor(message: str):
    # bring your own domain logic (regex, LLM, rules…)
    if "my name is" in message.lower():
        yield f"User: {message}"

async with run_fact_extraction(client, memory, my_extractor):
    # run your agent here; facts are extracted in the background
    ...
```

If you need per-session (not per-user) scope, or finer-grained
per-session-extracting-into-per-tenant-memory routing, provide your own
implementation of the `AgentMemory` protocol instead of using
`KurrentDBAgentMemory` directly — the subscription itself is a single
server-side group per process, so all session traffic flows through the one
extractor you register.

See `samples/fact_extraction.py` for a runnable personal-assistant demo.

## Run tests

```bash
pytest
```
