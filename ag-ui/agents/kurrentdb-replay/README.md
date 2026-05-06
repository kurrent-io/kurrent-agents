# @kurrent-io/ag-ui-agent-kurrentdb-replay

[AG-UI Protocol](https://github.com/ag-ui-protocol/ag-ui)
`AbstractAgent` that replays canonical [KurrentDB](https://kurrent.io)
sessions as AG-UI events (DEV-1559).

The read-side counterpart of
[`@kurrent-io/ag-ui-middleware-kurrentdb`](../../middlewares/kurrentdb-middleware/)
(DEV-1558). Together they close the loop: anything written to KurrentDB
in the canonical schema — by the AG-UI middleware OR by any of this
monorepo's native integrations (MAF, ADK, Strands, OpenAI Agents,
Claude SDK) — can be rendered in any AG-UI-compatible UI (CopilotKit,
AG-UI Dojo, custom).

> Status: **draft / DEV-1559 v1**.

## Why

Capacitor renders KurrentDB sessions natively (DEV-1614) and is the
primary in-house viewer. This package serves the **AG-UI ecosystem**
audience: users who already have an AG-UI client (CopilotKit, Dojo,
…) and want to point it at our canonical sessions without adopting
Capacitor.

## Install

```bash
npm install @kurrent-io/ag-ui-agent-kurrentdb-replay @kurrent/kurrentdb-client @ag-ui/client
```

## Usage

```ts
import { KurrentDBClient } from '@kurrent/kurrentdb-client';
import { KurrentDBReplayAgent } from '@kurrent-io/ag-ui-agent-kurrentdb-replay';

const client = KurrentDBClient.connectionString(
  'kurrentdb://localhost:2113?Tls=false',
);

const agent = new KurrentDBReplayAgent({
  client,
  sessionId: 'session-abc123',
  // mode: 'live',           // optional, default 'catchup'
  // maxCount: 1024,         // optional max events to read in catchup
  // customPassthrough: true // unknown canonical events → CUSTOM events
});

// Plug into any AG-UI runtime that takes an AbstractAgent.
const result = await agent.runAgent({ runId: 'replay-001' });
```

## Mapping

Each canonical event becomes one or more AG-UI events. Replay is
**message-grained** — we don't fabricate token deltas (the canonical
schema doesn't carry them). Each text/tool-call event becomes a single
`*_START → _CONTENT/_ARGS → _END` triple.

| Canonical | AG-UI |
|---|---|
| `SessionStarted` | `RUN_STARTED` |
| `UserMessageReceived` | `TEXT_MESSAGE_START`(role=user) → `_CONTENT` → `_END` |
| `AssistantTextGenerated` | `TEXT_MESSAGE_START`(role=assistant) → `_CONTENT` → `_END` |
| `AssistantThinkingGenerated` | `REASONING_MESSAGE_START` → `_CONTENT` → `_END` (skipped if `encrypted=true` and `content` empty) |
| `AssistantToolCallsGenerated` | optional carrier `TEXT_MESSAGE_*` if `content` set; per call: `TOOL_CALL_START` → `_ARGS`(JSON delta) → `_END` |
| `ToolResultReceived` | `TOOL_CALL_RESULT` |
| `InterruptIssued` / `Resolved` | `CUSTOM` (`name=interrupt.issued` / `.resolved`) |
| `SubagentStarted` / `Completed` | `CUSTOM` (`name=subagent.started` / `.completed`) |
| `SessionContinuedAs` | `CUSTOM` (`name=session.continued_as`) |
| `SessionEnded` | `RUN_FINISHED` (idempotent) |
| Other / framework-specific | `CUSTOM` (`name=canonical.<EventType>`) |

## Modes

- **catchup** (default) — read forward from the start of the session
  stream until the end, emit everything, then `RUN_FINISHED`. Best for
  replay-style UIs (eval, audit, time-travel debugging).
- **live** — `subscribeToStream` from the start, so the client gets
  catch-up plus live tail in one consistent ordering. The Observable
  stays open until `SessionEnded` lands or the consumer unsubscribes.
  Best for "follow this in-progress session" UIs.

In both modes the agent dedupes by `message_id`, so the catch-up window
of a live subscription doesn't double-emit events that already fired.

## Sample

`samples/round-trip.ts` is the full demo:

1. Synthetic AG-UI agent emits 15 events.
2. DEV-1558 middleware captures them → 6 canonical events in KurrentDB.
3. This package's `KurrentDBReplayAgent` reads those 6 → 15 AG-UI events again.

```bash
cd ../../../demo && docker compose up -d   # KurrentDB on :2113
cd ../ag-ui/agents/kurrentdb-replay
npm install
npm run sample
```

Output (abridged):

```
=== Original AG-UI events (15, written by ...) ===
  RUN_STARTED, TEXT_MESSAGE_*, TEXT_MESSAGE_*, TOOL_CALL_*,
  TOOL_CALL_RESULT, TEXT_MESSAGE_*, RUN_FINISHED

=== Replayed AG-UI events (15, by ...) ===
  RUN_STARTED, TEXT_MESSAGE_*, TEXT_MESSAGE_*, TOOL_CALL_*,
  TOOL_CALL_RESULT, TEXT_MESSAGE_*, RUN_FINISHED

15 → 6 canonical events on disk → 15 replayed events.
```

## Develop

```bash
cd ag-ui/agents/kurrentdb-replay
npm install
npm run build           # tsc to dist/
npm test                # vitest (translator + integration)

# Integration tests need KurrentDB on :2113:
cd ../../../demo && docker compose up -d
```

## License

Apache-2.0.
