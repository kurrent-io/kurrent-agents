# `ag-ui/` — AG-UI Protocol × KurrentDB integration

Kurrent's integration with the
[AG-UI Protocol](https://github.com/ag-ui-protocol/ag-ui), the
event-based wire format for connecting AI agents to user-facing
applications.

The integration ships as **two TypeScript packages**, deliberately
split because they implement different AG-UI base classes
(`Middleware` vs `AbstractAgent`) — different positions in AG-UI's
plugin architecture, even though both bridge AG-UI ↔ KurrentDB.

## What's in here

| Folder | Package | AG-UI base | Direction | Linear |
|---|---|---|---|---|
| [`middlewares/kurrentdb-middleware/`](./middlewares/kurrentdb-middleware/) | `@kurrent-io/ag-ui-middleware-kurrentdb` | `Middleware` | AG-UI agent → KurrentDB (write) | DEV-1558 |
| [`agents/kurrentdb-replay/`](./agents/kurrentdb-replay/) | `@kurrent-io/ag-ui-agent-kurrentdb-replay` | `AbstractAgent` | KurrentDB → AG-UI client (read) | DEV-1559 |
| [`interop-tests/`](./interop-tests/) | (Python pytest harness) | n/a | cross-language acceptance | DEV-1558 acceptance |

Each package has its own README with API, install, and usage. Together
they close the loop: an AG-UI agent's events get persisted as canonical
KurrentDB events, and any AG-UI client (CopilotKit, AG-UI Dojo, your
own page) can replay any session — including sessions captured by
sibling integrations in this monorepo (MAF / ADK / Strands / OpenAI
Agents / Claude SDK).

The chat-UI showcase that uses both packages end-to-end against real
LLMs lives one directory up: [`demo/ag-ui-showcase/`](../demo/ag-ui-showcase/).

### `middlewares/kurrentdb-middleware/` — write side (DEV-1558)

`KurrentDBMiddleware` extends `@ag-ui/client`'s `Middleware` base
class. Wraps any AG-UI agent; observes events via `runNextWithState`;
appends canonical events to `AgentSession-{threadId}` in KurrentDB.
Single TS implementation captures sessions from every framework with
an AG-UI integration (LangGraph, Mastra, CrewAI, Claude Agent SDK,
MS Agent Framework, …) — that's the lever.

### `agents/kurrentdb-replay/` — read side (DEV-1559)

`KurrentDBReplayAgent` extends `@ag-ui/client`'s `AbstractAgent` base
class. Reads `AgentSession-{id}` from KurrentDB and emits AG-UI
events. Lets any AG-UI client render a session captured by *any*
writer in the monorepo — native MAF / ADK / Strands integrations OR
the middleware capturing AG-UI agents. Two modes: `catchup` (default)
for replay/audit/eval, and `live` for following an in-progress
session.

### `interop-tests/` — Python acceptance harness

Spawns the middleware's `npm run interop:write` to write canonical
events, then verifies the result two ways: parsing each event with
Python's `kurrent-agent-schema` (proves wire format), and
reconstructing `Message[]` via MAF Python's
`KurrentDBHistoryProvider.get_messages()` (proves wire-compatibility
with an existing first-party Kurrent integration's reader). The
DEV-1558 acceptance criterion.

## Positioning — why these packages exist

AG-UI has **no built-in persistence** — hosts wire that up. kurrent-agents
already standardises durable, cross-framework-portable session storage.
The two protocols are unusually well-aligned: AG-UI events map almost
1:1 onto canonical kurrent events because they're solving the same
problem at different layers — AG-UI is the **wire protocol**,
kurrent-agents is the **durable record**. Marrying them gives every
AG-UI integration event-sourced durable conversations for free.

**UI positioning:** Kurrent sessions get their primary live UI via
**Capacitor** natively (DEV-1614) — no AG-UI dependency required.
AG-UI's complementary value is:

1. **Ecosystem reach** — CopilotKit, AG-UI Dojo, third-party clients
   consuming our sessions.
2. **Bidirectional interop** — third-party AG-UI clients talking to
   MAF / Strands / ADK / OpenAI Agents agents persisted via this
   integration.

This integration does *not* compete with Capacitor. It's about reach
into the AG-UI ecosystem.

## Linear workstream map

| ID | Title | Status |
|---|---|---|
| **DEV-1558** | AG-UI middleware → KurrentDB (write) | Shipped — `middlewares/kurrentdb-middleware/` |
| **DEV-1559** | KurrentDB → AG-UI session replay (read) | Shipped — `agents/kurrentdb-replay/` |
| DEV-1562 | State round-trip (`STATE_SNAPSHOT` / `STATE_DELTA`) | Open. Middleware reserves an `onStateEvent` no-op hook so DEV-1562 attaches without restructuring. |
| DEV-1560 | EvalRun ↔ AG-UI `runId` alignment | Open. Middleware writes `$run_id` event metadata so EvalRun events can join. Eval-runner utility + canonical-schema docs follow. |

## v1 spec answers committed in code

The middleware commits to specific v1 defaults that resolved open
questions in the original Linear specs. Detailed in
[`middlewares/kurrentdb-middleware/README.md`](./middlewares/kurrentdb-middleware/README.md).
Headlines:

- `runId` → `$run_id` event metadata (no canonical schema change).
- Idempotency on resume → read existing stream forward from start;
  in-memory `messageId` dedup, claimed synchronously before persist.
- Aborted runs → flush partial messages then `SessionEnded(reason="error")`
  with `extensions.ag_ui.aborted=true`.
- Persistence boundary → defer all message writes to `RUN_FINISHED` /
  `RUN_ERROR` (AG-UI may upgrade text-only assistant messages to
  text+toolCalls *after* `TEXT_MESSAGE_END`).
- Replay direction is message-grained — no synthesised token deltas.
- Unknown canonical events → `CUSTOM` AG-UI events; opt-out via
  `customPassthrough: false`.

## Open follow-ups

- **`schema/typescript/`** — both packages hand-write canonical types
  in `src/types.ts`. Replace with proto-generated bindings via
  `buf-gen-es`, mirroring `schema/python/` and `schema/dotnet/`. The
  cross-language CI workflow at
  `.github/workflows/schema-cross-language.yml` already exists and
  will gate drift once TS lands.
- **DEV-1562 (state round-trip)** — implement persistence + rehydration
  via the middleware's reserved `onStateEvent` hook.
- **DEV-1560 (EvalRun ↔ runId)** — eval-runner utility (read an
  `AgentSession` stream → emit `EvalRun-*` events per `runId`); document
  the `runId ↔ EvalRun.run_id` mapping in `schema/SCHEMA_v2.md`.
- **`MESSAGES_SNAPSHOT` on connect** for `KurrentDBReplayAgent` — Linear
  v1 spec mentions it for fast hydration; today the agent emits
  incrementally from the start with no upfront snapshot.
- **`kurrent_google_adk` integration broken against schema 0.4.0** —
  pre-existing bug surfaced when wiring the showcase ADK lane to real
  Claude. Tracked in [issue #58](https://github.com/kurrent-io/kurrent-agents/issues/58)
  along with the cross-framework deserialisation issue.
- **Discoverability** — publish both packages to npm; submit a docs PR
  to AG-UI pointing at this integration.
- **AG-UI repo design doc** — capture the v1 spec answers above in
  `docs/superpowers/specs/` of the AG-UI repo for community review.
