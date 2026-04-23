# kurrent-claude-agent-sdk

KurrentDB `SessionStore` adapter for the [Claude Agent SDK (Python)](https://github.com/anthropics/claude-agent-sdk-python). Mirrors every JSONL transcript line the Claude Code CLI writes locally out to a KurrentDB stream; `load` reconstructs entries on `--resume`.

**Status: v0.** `append` + `load` are implemented and round-trip entry dicts verbatim; verified end-to-end against `claude-agent-sdk >= 0.1.65` (the first release exposing the `SessionStore` protocol). `list_sessions`, `delete`, `list_subkeys`, and `list_session_summaries` are deliberately absent — the SDK's protocol probes for them at runtime and skips when missing.

Shares the canonical schema with the rest of the monorepo — see [`schema/SCHEMA.md`](../../schema/SCHEMA.md). The CLI's JSONL format is documented internal-and-unstable, so entries are stored deep-equal via JSON round-trip inside `ClaudeSDKEntry` framework-specific events — this matches the SDK's `load(append(entries)) == entries` guarantee (deep-equal, not byte-equal, per the `SessionStore` protocol docs). Canonical decomposition (so cross-framework readers see a normal conversation) is planned as a read-side subscriber; the mapping and rationale are recorded on DEV-1508.

## Design

Full design spec: [`DESIGN.md`](./DESIGN.md).

## Install (from source)

```bash
pip install -e ".[dev]"
```

## Usage

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

## Canonical decomposition (cross-framework reads)

`ClaudeSDKEntry` events preserve the CLI's JSONL shape verbatim. Cross-framework readers want canonical conversation events instead — `kurrent_claude_agent_sdk.decompose` turns a raw entry dict into the schema's `UserMessageReceived` / `AssistantTextGenerated` / `AssistantToolCallsGenerated` / `ToolResultReceived` events, plus a `$usage` metadata shim for assistant turns.

```python
from kurrent_claude_agent_sdk import decompose_stream

# ``entries`` is any iterable of raw JSONL dicts — e.g. ClaudeSDKEntry.raw_entry
# values fetched from a session stream.
for event, metadata in decompose_stream(entries):
    # event: a Pydantic canonical event (SCHEMA.md §3)
    # metadata: optional {"$usage": {...}} dict for KurrentDB event metadata
    ...
```

Pure functions — no I/O, no subscriber wiring. Caller owns the sink: typical consumers run this against an already-persisted `ClaudeSDKEntry` stream and append the decomposed events to a parallel canonical stream. Full mapping table: [`DESIGN.md §4.1`](./DESIGN.md#41-mapping).

## Run tests

```bash
docker compose up -d
pytest
```
