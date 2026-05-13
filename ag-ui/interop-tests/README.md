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

Each test spawns `npm run interop:write -- <session-id> <run-id>` from
the middleware package, which runs a synthetic AG-UI agent (RUN_STARTED
→ user message → tool call → tool result → final answer → RUN_FINISHED)
wrapped with `KurrentDBMiddleware`. The middleware appends canonical
events to `AgentSession-{session-id}`. Then:

### Test 1 — schema-level read

Python reads the stream via `AsyncKurrentDBClient` and parses each
event with `kurrent_agent_schema.from_json`, asserting:
- Event sequence: `SessionStarted` → `UserMessageReceived` →
  `AssistantToolCallsGenerated` → `ToolResultReceived` →
  `AssistantTextGenerated` → `SessionEnded`.
- Each event is the expected proto class with the expected field values.
- Every event's metadata carries `$run_id` (DEV-1560 prep) and
  `$schema_version=2`.

### Test 2 — real-integration read (MAF Python)

The same stream is fed through MAF Python's
`KurrentDBHistoryProvider.get_messages()` — the production reader path
the MAF integration uses. Asserts that reconstructed `Message[]` has:
- Correct role sequence (`user → assistant → tool → assistant`).
- User text preserved verbatim.
- Assistant message with a `function_call` content block carrying
  `name=get_weather`, `call_id=c1`, arguments dict.
- Carrier text ("Looking up.") preserved as a sibling text block.
- Tool result content block with matching `call_id` and JSON result.
- Final assistant text.

The middleware and MAF Python don't import each other — the canonical
schema is their only contract. If this test passes, the middleware is
wire-compatible with an existing first-party Kurrent integration's
reader path.

## Why it lives here, not in the middleware package

The middleware package is npm/TypeScript-only. The Python schema package
is the authoritative reader. Putting the cross-language test under
`ag-ui/interop-tests/` keeps it independent of either side's release
cadence and makes future additions (e.g., .NET-side reader) symmetrical.
