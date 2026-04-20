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

```python
from kurrent_agent_framework import (
    KurrentDBAgentMemory, run_fact_extraction,
)

memory = KurrentDBAgentMemory(client)

def my_extractor(message: str):
    # bring your own domain logic (regex, LLM, rules…)
    if "my name is" in message.lower():
        yield f"User: {message}"

async with run_fact_extraction(client, memory, my_extractor):
    # run your agent here; facts are extracted in the background
    ...
```

See `samples/fact_extraction.py` for a runnable personal-assistant demo.

## Run tests

```bash
pytest
```
