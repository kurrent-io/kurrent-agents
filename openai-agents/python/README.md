# kurrent-openai-agents

KurrentDB integration for the [OpenAI Agents SDK (Python)](https://github.com/openai/openai-agents-python) — persist agent sessions as canonical events, wire-compatible with the other integrations in this repo (Google ADK, Microsoft Agent Framework, Strands).

**Status: alpha.** `KurrentDBSession` implements the SDK's `Session` protocol (`get_items` / `add_items` / `pop_item` / `clear_session`) so it drops into `Runner(..., session=...)` unchanged. Items are decomposed into canonical events on write and reconstructed (with the original `raw_item` preserved under `extensions.openai`) on read.

Shares the canonical schema with the rest of the monorepo — see [`schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md). A session written by an OpenAI Agents SDK agent is readable by ADK / AFW / Strands agents for the conversational parts.

## Design

Full design spec: [`DESIGN.md`](./DESIGN.md).

## Install (from source)

```bash
pip install -e ".[dev]"
```

## Usage

```python
from agents import Agent, Runner
from kurrent_openai_agents import KurrentDBSession, client as kdb_client

kdb = kdb_client.from_connection_string("kurrentdb://localhost:2113?Tls=false")
session = KurrentDBSession(
    session_id="chat-1",
    client=kdb,
    app_name="my_app",
    user_id="alice",
)

agent = Agent(name="assistant", instructions="...")
result = await Runner.run(agent, "Hello", session=session)
```

## Run tests

```bash
docker compose up -d
pytest
```
