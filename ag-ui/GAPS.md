# AG-UI integration — gaps and strategic positioning

This document captures (a) where AG-UI fits in the kurrent-agents thesis,
(b) the four workstreams that make the integration whole, (c) what
[`ag-ui/python/`](./python/) (PR #48) closes and what it doesn't, and
(d) open spec questions that have to be answered before building the
primary lever (`ag-ui/middlewares/kurrentdb-middleware/`, TypeScript).

> Living document. Promote items into per-package DESIGN.md files as they
> get nailed down; remove them from here when they ship.

---

## 1. Why AG-UI at all

AG-UI has **no built-in persistence** — hosts wire that up. kurrent-agents
already standardises durable, cross-framework-portable session storage. The
two protocols are unusually well-aligned: AG-UI events map almost 1:1 onto
canonical kurrent events because they're solving the same problem at
different layers — AG-UI is the **wire protocol**, kurrent-agents is the
**durable record**. Marrying them gives every AG-UI integration
event-sourced durable conversations for free.

### UI positioning (clarified 2026-04-26)

Kurrent sessions get their primary live UI via **Capacitor** natively
(see DEV-1614) — no AG-UI dependency required. AG-UI's complementary value
is:

1. **Ecosystem reach** — CopilotKit, AG-UI Dojo, third-party clients
   consuming our sessions.
2. **Bidirectional interop** — third-party AG-UI clients talking to
   MAF / Strands / ADK / OpenAI Agents agents persisted via this integration.

This document does *not* try to compete with Capacitor. It's about reach
into the AG-UI ecosystem.

---

## 2. The four workstreams

| ID | Title | Direction | Language | Status | Priority |
|---|---|---|---|---|---|
| **DEV-1558** | AG-UI middleware → KurrentDB | write | TypeScript | design | **primary lever** — single TS impl, ~15× framework reach (LangGraph, Mastra, CrewAI, Claude Agent SDK, MS AF, …) |
| DEV-1562 | State round-trip (`STATE_SNAPSHOT` / `STATE_DELTA` persist + rehydrate) | both | TS + canonical | design | companion to DEV-1558 |
| DEV-1560 | EvalRun ↔ AG-UI `runId` alignment | cross-cutting | schema + readers | design | unblocks eval UIs |
| DEV-1559 | KurrentDB → AG-UI session replay | read | Python (this PR) | shipped (v0.0.1) | **lower priority** post-DEV-1614 — kept as ecosystem-reach scaffolding |

The middleware (DEV-1558) is the lever. Everything else is supporting work
that becomes valuable once middleware-captured sessions exist.

---

## 3. Gaps within DEV-1559 (the read side this PR ships)

The Python read-side bridge in `ag-ui/python/` ([PR #48](https://github.com/kurrent-io/kurrent-agents/pull/48))
works for the catch-up replay case but does not close the full DEV-1559
scope:

| Gap | Impact | Notes |
|---|---|---|
| `live=True` not integration-tested | Live-tail untrusted in prod | Catch-up path is asserted; live path is wired but unverified end-to-end |
| `runId == session_id` | Multi-run sessions render as one run in CopilotKit-style UIs | See §5 — recommended fix is `$run_id` event metadata |
| No `MESSAGES_SNAPSHOT` for late-joining clients | Reconnect mid-session yields nothing until the next event lands | Need a snapshot synthesised from canonical history on subscribe |
| Subagent streams not traversed | `SubagentStarted` becomes `CUSTOM` but `AgentSubsession-` stream isn't followed | Either inline subagent events into parent stream output, or expose a separate endpoint per `agent_id` |
| `$usage` metadata dropped | Token counts invisible in AG-UI consumers | AG-UI has no canonical usage event — surface as `RAW` or `CUSTOM` |
| No splice of `EvalRun-{run_id}` events | Eval scores not interleaved with session timeline | Needs DEV-1560 alignment first |
| State events untouched | Hosted-state agents (LangGraph, CrewAI flows) lose their state model in replay | Tracked under DEV-1562 |

These don't block PR #48 from landing — they are the follow-up backlog
for a complete DEV-1559.

---

## 4. Open spec questions for DEV-1558 (middleware)

To be answered in the design doc (target: `ag-ui` repo's
`docs/superpowers/specs/`) before code lands.

### 4.1 `runId` treatment — **recommend metadata, not new events**

AG-UI's `runId` is per-execution; a thread (≈ canonical session) can have
many runs. Two options:

| Option | Cost | Verdict |
|---|---|---|
| Add `RunStarted` / `RunFinished` to canonical schema v2.x | Schema bump + lockstep adoption across ADK / MAF / Strands / OpenAI / Claude SDK; most have no native "run" concept distinct from session | Heavy |
| **Stamp `$run_id` on event metadata** between AG-UI's `RUN_STARTED` and `RUN_FINISHED` | Additive, zero schema change, EvalRun joins on metadata | **Recommended** |

Schema-event option stays available as a fallback if a use case forces it.
DEV-1560 (EvalRun ↔ runId) is unblocked either way.

### 4.2 Multi-tool-call assistant messages (must be v1)

AG-UI emits N independent `TOOL_CALL_*` triples per assistant message;
canonical `AssistantToolCallsGenerated.tool_calls` is a single array.
Middleware must batch consecutive AG-UI tool calls sharing
`parentMessageId` (and any preceding `TEXT_MESSAGE_*` on the same
`parentMessageId`) into one canonical event.

Getting this wrong fragments the canonical stream and breaks
Python/.NET reader round-trip. **This is the acceptance test in
`interop-tests/`.**

### 4.3 Idempotency on resume

`RunAgentInput.messages` and `resume[]` mean the middleware can see a user
message that's already in the stream. v1 needs a dedup strategy:

- Read stream tail on session start, build an in-memory `messageId` set; or
- Bloom-filter-style dedup per thread; or
- Conditional append using `current_version` + retry on
  `WrongCurrentVersion` (matches ADK pattern).

Without this, replays double-write user messages.

### 4.4 Aborted runs

If `RUN_ERROR` fires mid-message (between `TEXT_MESSAGE_START` and
`_END`), the middleware sees no commit boundary. Pick a v1 policy:

- **Drop** the partial (clean stream, lose the partial output).
- **Persist with `extensions.ag_ui.aborted=true`** (preserves partial
  for forensics).
- **Emit `SessionEnded(reason="error")`** if the whole run aborted.

Recommended: persist partial with `aborted=true` extension flag — matches
the canonical schema's "preserve, don't lose" bias.

### 4.5 Per-framework usage extraction

AG-UI has no canonical usage event. Frameworks surface tokens via
`RAW` / `CUSTOM` with framework-specific shapes. v1 options:

- Small per-framework registry inside the middleware (LangGraph, Mastra, …).
- "Best-effort, framework-dependent" caveat in v1; `$usage` populated
  only when the middleware recognises the framework's signal.

Recommended: explicit caveat in v1, registry lands when the first user
asks for it.

### 4.6 State observation hook (must be v1, even if implemented in DEV-1562)

DEV-1562 defers `STATE_SNAPSHOT` / `STATE_DELTA` persistence, but the
middleware needs the *hook* in v1 — otherwise that work lands as a
refactor. One-paragraph sketch goes in the design doc; recommended
shape:

```ts
interface MiddlewareHooks {
  onCommittedMessage?(msg: AGUIMessage, ctx: SessionContext): Promise<void>;
  onStateEvent?(event: StateSnapshot | StateDelta, ctx: SessionContext): Promise<void>;
}
```

DEV-1562 implements `onStateEvent`; v1 leaves it as a no-op extension point.

---

## 5. Layout decisions pending

Current state has `ag-ui/python/` (read-side, this PR). Adding a TS
middleware at `ag-ui/middlewares/kurrentdb-middleware/` produces an inconsistent
layout (one organised by language, one by role). Two reorganisation
options before middleware lands:

### Option A — by role under `ag-ui/`

```
ag-ui/
  middlewares/kurrentdb-middleware/   # TS write-side (DEV-1558)
  replay/python/                # current ag-ui/python/, renamed
  interop-tests/                # cross-direction round-trips (DEV-1558 acceptance)
schema/
  python/                       # existing
  dotnet/                       # existing
  typescript/                   # NEW — generated from schema/proto/, npm-published
```

Pro: layout reflects what each package *is*. Con: rename of existing dir.

### Option B — by language under `ag-ui/`

```
ag-ui/
  python/                       # current; replay
  typescript/kurrentdb-middleware/  # TS write-side
  typescript/schema/            # OR keep at schema/typescript/
  interop-tests/
```

Pro: no rename of existing dir. Con: middleware and schema are different
release units; co-locating by language confuses npm publish boundaries.

**Recommended: Option A.** The schema TS package belongs at
`schema/typescript/` (mirroring `schema/python` and `schema/dotnet` —
proto-generated packages stay together; cross-language CI already exists
to gate drift). The rename of `ag-ui/python/` → `ag-ui/replay/python/`
happens in lockstep with the middleware design doc landing.

`interop-tests/` could alternatively live at repo root if it grows
beyond AG-UI; `ag-ui/interop-tests/` is fine for v1.

---

## 6. Discoverability

Middleware in this repo loses AG-UI's first-party discovery surface.
Mitigations:

- Publish to npm as `@kurrent-io/ag-ui-middleware-kurrentdb` (or similar) —
  AG-UI users find it via npm search.
- Submit a docs PR to AG-UI pointing at this repo from their middlewares /
  integrations page.
- If AG-UI maintains a "community middlewares" list, get listed.

Worth surfacing in the design doc under "distribution."

---

## 7. Acceptance criterion (DEV-1558)

> Wrapping any existing AG-UI `/integrations/*` agent with this middleware
> produces a replayable `AgentSession-*` stream readable by kurrent-agents'
> existing Python/.NET readers.

Implemented as a round-trip test in `ag-ui/interop-tests/` (or
`interop-tests/` at repo root): a TS-side test runs an AG-UI agent through
the middleware, then a Python-side test reads the same stream via
`kurrent-agent-schema` and asserts structural equivalence (same canonical
events in the same order, same field values, modulo `extensions.ag_ui.*`
which is allowed to add fields).

This is the single forcing function that says the middleware actually
works.

---

## 8. What goes in the design doc vs here

- **Here (`GAPS.md`):** strategic positioning, workstream priorities, open
  questions cross-cutting multiple workstreams, layout / discoverability
  decisions affecting the repo.
- **Design doc (per workstream, in `docs/superpowers/specs/`):** the
  resolved decisions for that workstream's v1 — runId metadata format,
  multi-tool-call batching algorithm, dedup strategy, abort policy,
  per-framework usage table, hook signatures, acceptance harness.

When a question in §4 or §5 is resolved, it moves out of here into a
design doc; this file shrinks over time.
