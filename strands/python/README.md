# kurrent-strands

KurrentDB integration for the [Strands Agents SDK](https://github.com/strands-agents/sdk-python) — persist agent sessions as canonical events in KurrentDB, wire-compatible with the other integrations in this repo (Google ADK, Microsoft Agent Framework).

**Status: alpha.** `KurrentDBSessionManager` implements the core `initialize` / `append_message` / `sync_agent` / `redact_latest_message` surface so messages round-trip through KurrentDB; `KurrentDBAgentMemory` provides cross-session fact recall. Reasoning content emits canonical `AssistantThinkingGenerated`, and tool-approval pauses emit canonical `InterruptIssued` / `InterruptResolved` per `SCHEMA_v2.md §3.2`–`§3.3`. Strands-specific state (conversation-manager state, custom metadata, non-canonical content blocks) rides in `extensions.strands`; runtime-only state (`_internal_state`) round-trips via the framework-specific `StrandsAgentState` event.

Shares the canonical schema with the rest of the monorepo — see [`schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md). A session written by a Strands agent is readable by an ADK agent (and vice versa) for the conversational parts.

## Design

Full design spec: [`DESIGN.md`](./DESIGN.md).

## Install (from source)

```bash
pip install -e ".[dev]"
```

## Usage

```python
from strands import Agent
from kurrent_strands import KurrentDBSessionManager, client as kdb_client

client = kdb_client.from_connection_string("kurrentdb://localhost:2113?Tls=false")
sm = KurrentDBSessionManager(
    client=client,
    session_id="chat-1",
    app_name="my_app",
    user_id="alice",
)

agent = Agent(
    model="anthropic.claude-haiku-4-5",
    session_manager=sm,
)
```

## Run tests

```bash
docker compose up -d   # start KurrentDB
pytest
```

## Why the SessionManager methods are sync

Strands' `SessionManager` is invoked from synchronous hook callbacks. Methods on our `KurrentDBSessionManager` use the **sync** `KurrentDBClient` (not the async variant the ADK integration uses) to match. Bridging is possible but not worth the complexity for the first pass.
