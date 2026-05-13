# AG-UI integration — gaps and strategic positioning

This document captures (a) where AG-UI fits in the kurrent-agents
thesis, (b) the four workstreams that make the integration whole,
(c) what this PR's v1 packages
([`ag-ui/middlewares/kurrentdb-middleware/`](./middlewares/kurrentdb-middleware/) for write,
[`ag-ui/agents/kurrentdb-replay/`](./agents/kurrentdb-replay/) for read)
ship and what they don't, and (d) open spec questions whose v1 answers
are now committed in code (and which still need design-doc treatment in
the AG-UI repo).

> Living document. Promote items into per-package DESIGN.md files as they
> get nailed down; remove them from here when they ship.

---

## 1. Why AG-UI at all

AG-UI has **no built-in persistence** — hosts wire that up. kurrent-agents
already standardises durable, cross-framework-portable session storage.
The two protocols are unusually well-aligned: AG-UI events map almost 1:1
onto canonical kurrent events because they're solving the same problem at
different layers — AG-UI is the **wire protocol**, kurrent-agents is the
**durable record**. Marrying them gives every AG-UI integration
event-sourced durable conversations for free.

### UI positioning (clarified 2026-04-26)

Kurrent sessions get their primary live UI via **Capacitor** natively
(see DEV-1614) — no AG-UI dependency required. AG-UI's complementary
value is:

1. **Ecosystem reach** — CopilotKit, AG-UI Dojo, third-party clients
   consuming our sessions.
2. **Bidirectional interop** — third-party AG-UI clients talking to
   MAF / Strands / ADK / OpenAI Agents agents persisted via this
   integration.

This document does *not* try to compete with Capacitor. It's about reach
into the AG-UI ecosystem.

---

## 2. The four workstreams

| ID | Title | Direction | Language | Status |
|---|---|---|---|---|
| **DEV-1558** | AG-UI middleware → KurrentDB | write | TypeScript | **v1 in this PR** at `ag-ui/middlewares/kurrentdb-middleware/` |
| **DEV-1559** | KurrentDB → AG-UI session replay | read | TypeScript | **v1 in this PR** at `ag-ui/agents/kurrentdb-replay/` |
| DEV-1562 | State round-trip (`STATE_SNAPSHOT` / `STATE_DELTA` persist + rehydrate) | both | TS + canonical | open; v1 reserves an observation hook |
| DEV-1560 | EvalRun ↔ AG-UI `runId` alignment | cross-cutting | schema + readers | open; middleware stamps `$run_id` metadata so EvalRun can join |

The middleware (DEV-1558) is the lever — single TS implementation, ~15×
framework reach (every AG-UI integration: LangGraph, Mastra, CrewAI,
Claude Agent SDK, MS Agent Framework, …).

---

## 3. v1 defaults committed in code

Each of these resolves an open spec question from earlier drafts. The
authoritative reference is the middleware source; this section
summarises for context.

### 3.1 `runId` → event metadata (no canonical schema change)

Every appended event carries `$run_id` on KurrentDB metadata, alongside
`$schema_version`. EvalRun events join on this metadata without
requiring new canonical event types. See `src/middleware.ts` `persist()`.

### 3.2 Multi-tool-call batching — built into AG-UI

`AssistantMessage.toolCalls[]` is already a batched array in AG-UI's
message model. The middleware sees the final assistant message form at
`RUN_FINISHED` and emits one `AssistantToolCallsGenerated` per message.
No incremental batching logic needed.

### 3.3 Idempotency on resume — read tail + dedup

On first session touch, the middleware reads the existing
`AgentSession-{id}` stream and seeds a per-session
`Set<messageId>`. New messages are added to the set synchronously
*before* persist begins, eliminating in-flight races. See `src/dedup.ts`.

### 3.4 Aborted runs — partial flush + extension flag

`RUN_ERROR` triggers `flushMessagesAndEnd('error')`: any messages in the
run's final state get persisted (skipping ones already in the dedup set),
followed by `SessionEnded(reason="error")` with
`extensions.ag_ui.aborted = true`. Same path on inner-Observable error.

### 3.5 Persistence boundary — RUN_FINISHED, not per-event

AG-UI emits messages incrementally and may *upgrade* an assistant message
from text-only to text+toolCalls after `TEXT_MESSAGE_END` (when subsequent
`TOOL_CALL_*` events fire with the same `parentMessageId`). Persisting
per-event would require an "update" semantics on canonical streams (which
the schema doesn't support — it's append-only). Persisting at
`RUN_FINISHED` sees each message in its final form.

**Trade-off**: no partial durability if the host process dies mid-run.
Acceptable for v1; revisit if a use case forces it.

### 3.6 Pipeline serialisation

A single per-run `Promise` chain orders all KurrentDB writes:
`onRunStarted → flushMessagesAndEnd`. This eliminates the race between
`ensureInitialised` (async stream tail read) and message persistence
that an earlier draft had. See `src/middleware.ts`.

### 3.7 Token usage — best-effort, framework-dependent

AG-UI has no canonical usage event. Frameworks surface tokens via `RAW` /
`CUSTOM` events with framework-specific shapes. v1 captures nothing
beyond what the AG-UI event itself carries. Per-framework adapters land
when the first user asks for them.

### 3.8 State events — DEV-1562 hook reserved

`STATE_SNAPSHOT` / `STATE_DELTA` (RFC 6902 patches) and
`MESSAGES_SNAPSHOT` are observed but not persisted in v1. The middleware
exposes a public `onStateEvent` hook (see `KurrentDBMiddlewareOptions`)
that fires for each of these events, carrying the AG-UI event plus the
post-event accumulated state, messages, sessionId, and runId. Default
is no-op. DEV-1562 implements its persistence by passing a hook
implementation, no middleware refactor needed.

### 3.9 SessionStarted enrichment

The middleware fills `app_name`, `agent_name`, `user_id`, `model`, and
`agent_config` on `SessionStarted` from a combination of constructor
options, `RunAgentInput.context` lookup (`description: "user_id" |
"model"`), `RunAgentInput.forwardedProps.model`, and `RunAgentInput.tools`.
`AgentConfig.tools` map AG-UI's `Tool` to canonical `ToolSpec` with
`source: "ag_ui"`; `forwardedProps` (minus `model`) lift into
`AgentConfig.model_parameters`. Both fields are dropped from the wire
payload when nothing useful is derivable.

### 3.10 DEV-1559 replay defaults

Inverse mappings of the middleware. Two open spec questions resolved:

* **One-shot, not synthesised token deltas.** Each canonical event
  becomes a complete `*_START → _CONTENT/_ARGS → _END` triple in a
  single tick. Canonical events are message-grained; fabricating fake
  token deltas would invent data we don't have. UIs that want a
  streaming feel can synthesise word-by-word client-side.
* **Unknown events → `CUSTOM` by default**, opt-out via
  `customPassthrough: false`. Lets downstream UIs surface ADK / MAF /
  Strands extras without dropping data.

Modes:

* `catchup` (default) — read forward to end, emit, finish. For
  replay/audit/eval.
* `live` — `subscribeToStream` from start; catch-up + live tail in
  one consistent ordering. Dedup by `message_id` makes the catch-up
  window harmless. For "follow this in-progress session" UIs.

---

## 4. Layout

```
ag-ui/
  middlewares/
    kurrentdb-middleware/        # TS write-side (DEV-1558) — this PR
  agents/
    kurrentdb-replay/            # TS read-side (DEV-1559) — this PR
  interop-tests/                 # TS-writes / Python-reads acceptance harness
  GAPS.md                        # this file
demo/
  ag-ui-showcase/                # 4-lane chat demo using both packages
schema/
  python/                        # existing (proto-generated)
  dotnet/                        # existing (proto-generated)
  typescript/                    # NEXT (TODO) — buf-gen-es from schema/proto/
```

`schema/typescript/` is the next-up follow-up: replaces the hand-written
canonical types in `middlewares/kurrentdb-middleware/src/types.ts` with
buf-generated bindings. The cross-language CI workflow at
`.github/workflows/schema-cross-language.yml` already exists and will
gate drift once TS lands.

---

## 5. Discoverability

The two TS packages in this repo lose AG-UI's first-party discovery
surface. Mitigations (post-merge):

- Publish to npm:
  - `@kurrent-io/ag-ui-middleware-kurrentdb` — write side (DEV-1558).
  - `@kurrent-io/ag-ui-agent-kurrentdb-replay` — read side (DEV-1559).
- Submit a docs PR to AG-UI pointing at this repo from their
  middlewares / integrations page.
- If AG-UI maintains a "community middlewares" list, get listed.

---

## 6. Acceptance (DEV-1558)

> Wrapping any existing AG-UI `/integrations/*` agent with this
> middleware produces a replayable `AgentSession-*` stream readable by
> kurrent-agents' existing Python/.NET readers.

**v1 status: covered.** Two layers of cross-language tests in
`ag-ui/interop-tests/`:

1. **Schema-level** — TS middleware writes; Python parses each event with
   `kurrent-agent-schema` and asserts proto types and field values.
2. **Real-integration** — TS middleware writes; **MAF Python's
   `KurrentDBHistoryProvider.get_messages()`** (the same code path MAF
   uses in production) reconstructs `Message` objects with proper
   `role`, `text`, `function_call`, and `function_result` content
   blocks. The middleware and MAF Python don't know each other exists;
   the canonical schema is their only contract.

This is stronger than the spec's "wrap a /integrations/* agent" path
because it composes two of our own first-party integrations on opposite
ends of a single canonical stream — exactly the cross-framework
portability claim the schema is designed to make.

A real-LLM smoke test of the read side via real framework runs is
the demo at [`demo/ag-ui-showcase/`](../demo/ag-ui-showcase/) — MAF
and Strands write canonical events through their native integrations,
the DEV-1559 replay agent live-tails them and emits AG-UI events to
the browser. ADK is wired similarly but its integration package
needs a fix (see §7).

---

## 7. What's still open

- **`schema/typescript/`** — replace hand-written canonical types in
  both TS packages (middleware + replay) with proto-generated
  bindings; published to npm. Cross-language CI gate is already in
  place for Python/.NET.
- **DEV-1562 (state round-trip)** — observation hook reserved on the
  middleware; impl follows once a concrete use case lands.
- **DEV-1560 (EvalRun ↔ runId)** — middleware writes `$run_id` metadata;
  reader-side joining and an eval-runner utility (read an
  `AgentSession` stream → emit `EvalRun-*` events per `runId`) follow.
  `schema/SCHEMA_v2.md` doesn't yet document the
  `AG-UI runId ↔ EvalRun.run_id` / `threadId ↔ session_id` mapping.
- **`MESSAGES_SNAPSHOT` on connect** in DEV-1559 — Linear v1 spec
  mentions it for fast hydration; current replay emits incrementally
  from the start with no upfront snapshot.
- **`kurrent_google_adk` integration broken against schema 0.4.0** —
  pre-existing bug surfaced when wiring the showcase ADK lane to real
  Claude. Imports `kurrent_agent_schema.events` which moved when
  schema went proto-generated. ADK lane in the demo is greyed out
  pending this fix. Not in any AG-UI Linear ticket.
- **LangGraph / Mastra real-AG-UI smoke test** — would exercise the
  DEV-1558 middleware (write side) end-to-end against a real AG-UI
  framework, complementing the read-side smoke test the demo already
  provides via the native lanes.
- **AG-UI repo design doc** — capture §3 decisions in
  `docs/superpowers/specs/` of the ag-ui repo for community review.

---

## 8. What goes in the design doc vs here

- **Here (`GAPS.md`):** strategic positioning, workstream priorities,
  cross-cutting decisions.
- **AG-UI repo design doc:** §3 v1 defaults written up for AG-UI
  community review (runId metadata, persistence boundary, idempotency,
  aborted runs).
- **Middleware README:** API, install, usage.

When a section here moves into one of the other two, delete it here.
