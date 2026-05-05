# ag-ui/interop-tests

Cross-language acceptance harness for DEV-1558.

The TypeScript middleware (`ag-ui/middlewares/kurrentdb-middleware/`)
writes canonical events to KurrentDB; the Python `kurrent-agent-schema`
package reads them back. If the round-trip succeeds, the wire format is
structurally identical and any Python or .NET integration in this
monorepo can consume sessions captured by the TS middleware.

This is the single forcing function that says "the middleware actually
works."

## Run

```bash
# 1. Start KurrentDB
cd ../../demo && docker compose up -d

# 2. Install middleware deps (one-time)
cd ../ag-ui/middlewares/kurrentdb-middleware && npm install

# 3. Run the interop test
cd ../../interop-tests
uv sync --extra dev
uv run pytest -v
```

## What it does

1. `pytest` spawns `npm run interop:write -- <session-id> <run-id>` from
   the middleware package; the writer runs a synthetic AG-UI agent
   (RUN_STARTED → user message → tool call → tool result → final
   answer → RUN_FINISHED) wrapped with `KurrentDBMiddleware`.
2. The middleware appends canonical events to
   `AgentSession-{session-id}`.
3. Python reads the same stream via `AsyncKurrentDBClient` and parses
   each event with `kurrent_agent_schema.from_json`, asserting:
   - Event sequence: `SessionStarted` → `UserMessageReceived` →
     `AssistantToolCallsGenerated` → `ToolResultReceived` →
     `AssistantTextGenerated` → `SessionEnded`.
   - Each event is the expected proto class with the expected field values.
   - Every event's metadata carries `$run_id` (DEV-1560 prep) and
     `$schema_version=2`.

## Why it lives here, not in the middleware package

The middleware package is npm/TypeScript-only. The Python schema package
is the authoritative reader. Putting the cross-language test under
`ag-ui/interop-tests/` keeps it independent of either side's release
cadence and makes future additions (e.g., .NET-side reader) symmetrical.
