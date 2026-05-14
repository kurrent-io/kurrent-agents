# kurrent-openai-agents

KurrentDB integration for the [OpenAI Agents SDK (Python)](https://github.com/openai/openai-agents-python) — persist agent sessions as canonical events, wire-compatible with the other integrations in this repo (Google ADK, Microsoft Agent Framework, Strands).

**Status: alpha.** `KurrentDBSession` implements both the SDK's `Session` protocol and `RunHooksBase`, so the same object drops into `Runner(..., session=s, hooks=s)` for both persistence and handoff promotion. Items decompose into canonical events on write; handoffs become `SubagentStarted` / `SubagentCompleted` on the parent and a dedicated `AgentSubsession-{id}-{agent_id}` stream. On read, the subsession transcript is re-flattened into the SDK's flat item list with the original `raw_item` preserved under `extensions.openai`.

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
# Pass `hooks=session` too to enable canonical handoff promotion
# (SubagentStarted/Completed + per-subagent stream). Without hooks the
# handoff still persists as a regular tool call on the parent stream.
result = await Runner.run(agent, "Hello", session=session, hooks=session)
```

## Run tests

```bash
docker compose up -d
pytest
```
