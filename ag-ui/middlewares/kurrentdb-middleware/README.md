# @kurrent-io/ag-ui-middleware-kurrentdb

[AG-UI Protocol](https://github.com/ag-ui-protocol/ag-ui) middleware that
persists agent sessions to [KurrentDB](https://kurrent.io) as canonical
events (DEV-1558).

A single TypeScript implementation captures sessions from every framework
that has an AG-UI integration (LangGraph, Mastra, CrewAI, MS Agent
Framework, Google ADK, …). The resulting `AgentSession-*` streams are
readable by every other Kurrent integration in this monorepo (Python,
.NET) — the wire format is the canonical schema in
[`schema/SCHEMA_v2.md`](../../../schema/SCHEMA_v2.md).

> Status: **draft / DEV-1558 v1**. See
> [`ag-ui/GAPS.md`](../../GAPS.md) for the full strategic framing and
> the open spec questions whose v1 defaults this implementation
> commits to.

## Install

```bash
npm install @kurrent-io/ag-ui-middleware-kurrentdb @kurrent/kurrentdb-client @ag-ui/client
```

## Usage

```ts
import { KurrentDBClient } from '@kurrent/kurrentdb-client';
import { KurrentDBMiddleware } from '@kurrent-io/ag-ui-middleware-kurrentdb';
import { MyAgent } from './my-agent.js';

const client = KurrentDBClient.connectionString(
  'kurrentdb://localhost:2113?Tls=false',
);

const agent = new MyAgent();
agent.use(
  new KurrentDBMiddleware({
    client,
    appName: 'weather_demo',
    agentName: 'WeatherAgent',
  }),
);

await agent.runAgent({ runId: 'run-001' });
// AgentSession-{threadId} now contains: SessionStarted →
// UserMessageReceived → AssistantToolCallsGenerated → ToolResultReceived
// → AssistantTextGenerated → SessionEnded.
```

## Persistence model

AG-UI emits messages incrementally (`TEXT_MESSAGE_START → CONTENT → END`,
then `TOOL_CALL_START → ARGS → END` may attach tool calls to the same
assistant message after `END`). The middleware **defers persistence to
`RUN_FINISHED` / `RUN_ERROR`** — at that point each message is in its
final form (text, text+toolCalls, or tool result), and we emit one
canonical event per message:

| AG-UI Message                              | Canonical event                  |
|--------------------------------------------|----------------------------------|
| `UserMessage`                              | `UserMessageReceived`            |
| `AssistantMessage` (text only)             | `AssistantTextGenerated`         |
| `AssistantMessage` (with `toolCalls[]`)    | `AssistantToolCallsGenerated`    |
| `ReasoningMessage`                         | `AssistantThinkingGenerated`     |
| `ToolMessage`                              | `ToolResultReceived`             |
| `SystemMessage`, `DeveloperMessage`        | (skipped in v1)                  |
| `ActivityMessage`                          | (skipped in v1; DEV-1562 hook)   |

The trade-off is "no partial durability" if the host process dies
mid-run. Aborted runs (`RUN_ERROR`) flush whatever messages have
committed and emit `SessionEnded` with `extensions.ag_ui.aborted=true`.

## v1 defaults (per ag-ui/GAPS.md §4)

| Question | This implementation |
|---|---|
| `runId` representation | Stamped on event metadata as `$run_id` (no canonical schema change) |
| Multi-tool-call batching | Trivial — `AssistantMessage.toolCalls[]` is already batched in AG-UI |
| Idempotency on resume | Read stream tail on first session touch, dedup by `messageId` |
| Aborted runs | Flush partial messages, then `SessionEnded(reason="error")` with `extensions.ag_ui.aborted=true` |
| Per-framework usage extraction | None in v1 (best-effort, framework-dependent — to be added per integration as needed) |
| `STATE_SNAPSHOT` / `STATE_DELTA` | Not in v1; observation hook reserved for DEV-1562 |

## Configuration

```ts
new KurrentDBMiddleware({
  client,                           // required — connected KurrentDBClient

  // Map AG-UI threadId → canonical session_id. Default: identity.
  scope: (input) => `${input.context?.find(c => c.description === 'tenant_id')?.value}-${input.threadId}`,

  appName: 'my-app',                // → SessionStarted.app_name
  agentName: 'my-agent',            // → SessionStarted.agent_name

  // → SessionStarted.model. Either a literal, or a function reading
  // from RunAgentInput. Default: forwardedProps.model, then context
  // entry { description: "model" }.
  model: 'claude-haiku-4-5',

  // → SessionStarted.agent_config. Default derives `tools` from
  // RunAgentInput.tools (mapped to canonical ToolSpec) and lifts
  // forwardedProps (minus `model`) into model_parameters.
  agentConfig: (input) => ({ tools: input.tools.map(t => ({ name: t.name })) }),

  // Reserved for DEV-1562 (state round-trip). Called for every
  // STATE_SNAPSHOT / STATE_DELTA / MESSAGES_SNAPSHOT event the inner
  // agent emits. Default: no-op. The middleware itself does not
  // persist state in v1.
  onStateEvent: ({ event, state, messages, sessionId, runId }) => { /* ... */ },

  logger: console.log,              // optional; receives lifecycle messages
});
```

## Sample

Run a synthetic agent through the middleware and see the AG-UI events
collapse into canonical events:

```bash
cd ../../demo && docker compose up -d   # KurrentDB on :2113
cd ../ag-ui/middlewares/kurrentdb-middleware
npm install
npm run sample
```

Output (abridged):

```
=== AG-UI events emitted by agent (15) ===
  RUN_STARTED
  TEXT_MESSAGE_START / _CONTENT / _END   user "What's the weather in Oslo?"
  TEXT_MESSAGE_START / _CONTENT / _END   assistant "Looking that up."
  TOOL_CALL_START / _ARGS / _END         get_weather({"city":"Oslo"})
  TOOL_CALL_RESULT                       {"temperature_c":8,...}
  TEXT_MESSAGE_START / _CONTENT / _END   assistant "8°C with light rain in Oslo."
  RUN_FINISHED

=== Canonical events persisted to AgentSession-sample-... ===
  [0] SessionStarted
  [1] UserMessageReceived          "What's the weather in Oslo?"
  [2] AssistantToolCallsGenerated  carrier="Looking that up." + 1 tool call
  [3] ToolResultReceived           {"temperature_c":8,"condition":"light_rain"}
  [4] AssistantTextGenerated       "8°C with light rain in Oslo."
  [5] SessionEnded                 reason=complete
```

15 incremental AG-UI events → 6 canonical events. Any Python or .NET
integration in this monorepo can now replay the session unchanged.

## Layout

| Path | Purpose |
|---|---|
| `src/translator.ts` | Pure: AG-UI `Message` → canonical event(s). No I/O. |
| `src/middleware.ts` | `KurrentDBMiddleware` extends `Middleware` from `@ag-ui/client` |
| `src/dedup.ts` | Per-thread `messageId` dedup, seeded from existing stream |
| `src/streamNames.ts` | Canonical stream name builders (mirrors Python/.NET) |
| `src/types.ts` | Canonical event types (hand-written stopgap; see TODO) |
| `samples/basic.ts` | Side-by-side AG-UI vs canonical events demo |
| `test/translator.test.ts` | Unit tests for the pure translator |
| `test/middleware.integration.test.ts` | End-to-end vs live KurrentDB |

## TODO

- **`schema/typescript/`**: `src/types.ts` is a hand-written stopgap.
  Replace with generated types from `schema/proto/` via `buf-gen-es`,
  mirroring `schema/python/` and `schema/dotnet/`. Tracked in
  [`ag-ui/GAPS.md`](../../GAPS.md) §5.
- **State round-trip (DEV-1562)**: persist `STATE_SNAPSHOT` /
  `STATE_DELTA` as canonical events; rehydrate on new runs.
- **Token usage**: per-framework registry mapping `RAW`/`CUSTOM` events
  with usage signals to `$usage` metadata.
- **Subagent streams**: AG-UI sub-agent events → `AgentSubsession-*`
  streams.
- **AG-UI repo discoverability**: publish to npm and submit a docs PR
  to AG-UI pointing at this middleware.

## Develop

```bash
cd ag-ui/middlewares/kurrentdb-middleware
npm install
npm run build              # tsc to dist/
npm test                   # vitest (translator + integration)

# Integration tests need KurrentDB on :2113:
cd ../../../demo && docker compose up -d
```

## License

Apache-2.0.
