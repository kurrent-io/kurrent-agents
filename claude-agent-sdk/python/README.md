# kurrent-claude-agent-sdk

KurrentDB `SessionStore` adapter for the [Claude Agent SDK (Python)](https://github.com/anthropics/claude-agent-sdk-python). Mirrors every JSONL transcript line the Claude Code CLI writes locally out to a KurrentDB stream; `load` reconstructs entries on `--resume`.

**Status: scaffolding.** `append` + `load` are implemented and round-trip the opaque entry dicts byte-for-byte. `list_sessions`, `delete`, and `list_subkeys` are stubbed with `NotImplementedError` (the SDK's protocol allows this — call sites probe for presence at runtime).

Shares the canonical schema with the rest of the monorepo — see [`schema/SCHEMA.md`](../../schema/SCHEMA.md). Because the CLI's JSONL format is internal and unstable per the SDK docs, entries are preserved verbatim inside `ClaudeSDKEntry` framework-specific events in v0. A follow-up pass can add canonical decomposition for recognised shapes.

## Design

Full design spec: [`DESIGN.md`](./DESIGN.md).

## Install (from source)

```bash
pip install -e ".[dev]"
```

## Usage (planned)

```python
from claude_agent_sdk import ClaudeAgentOptions, query
from kurrent_claude_agent_sdk import KurrentDBSessionStore, client as kdb_client

kdb = kdb_client.from_connection_string("kurrentdb://localhost:2113?Tls=false")
store = KurrentDBSessionStore(kdb, app_name="my_app", user_id="alice")

async for msg in query(
    prompt="Hello!",
    options=ClaudeAgentOptions(session_store=store),
):
    ...  # entries mirrored to KurrentDB as they arrive
```

## Run tests

```bash
docker compose up -d
pytest
```
