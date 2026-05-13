# OpenAI Agents — promote handoffs to canonical subagent events (AI-471)

**Status:** Design
**Linear:** [AI-471](https://linear.app/kurrent/issue/AI-471)
**Depends on (shipped):** [AI-475](https://linear.app/kurrent/issue/AI-475) — canonical schema v2 cutover for OpenAI Agents (PR #54, commit `7af2dcb`).
**Related:** [AI-621](https://linear.app/kurrent/issue/AI-621) — MAF .NET stream-name conformance follow-up (split out from this PR's schema-doc work).

## Problem

OpenAI Agents SDK models multi-agent flows as **handoffs**: an agent calls a tool whose name encodes a target agent, and the SDK switches the active agent for subsequent turns. The handoff and any items the target agent produces all flow into the parent session's flat item list — there is no separate stream boundary in the upstream model.

Schema v2 (`SCHEMA_v2.md §3.5`) canonicalises subagent lifecycle as `SubagentStarted` / `SubagentCompleted` events on the parent `AgentSession-` stream, with the subagent's transcript on a dedicated `AgentSubsession-{parent_session_id}-{agent_id}` stream. The AI-475 cutover deliberately deferred this promotion because it's a meaningful surface change beyond the mechanical proto-types swap.

Today's OpenAI Agents integration emits `handoff_call` / `handoff_output` items as framework-specific `OpenAIItem` events on the parent stream. Cross-framework readers (ADK, MAF, Strands, Claude SDK, Capacitor) cannot see the subagent lifecycle in canonical form, and Capacitor's trace-tree projector silently drops handoffs from its agent hierarchy view.

## Goals

1. Map `handoff_call` → `SubagentStarted` on the parent stream, atomically mirrored to the subsession stream.
2. Route the handoff target's items to `AgentSubsession-{parent_session_id}-{agent_id}` instead of letting them land on the parent.
3. Map `handoff_output` → `SubagentCompleted`, atomically mirrored to both streams.
4. Preserve the lossless `extensions.openai.raw_item` round-trip on the parent's `handoff_call` / `handoff_output` items — the SDK still sees a flat list it can replay.
5. Solidify cross-framework stream-name conventions in `SCHEMA_v2.md` so future integrations land compatible with Capacitor by construction.

## Non-goals

- **Nested handoffs.** Schema's resolved open-question #5 says flat one-level is sufficient. First seen nested handoff logs a WARN and degrades to `OpenAIItem`.
- **Streaming-mode integration test.** The state machine is streaming-safe by design (see Concurrency); a dedicated streaming Testcontainers test is deferred to a follow-up.
- **MAF .NET stream-name conformance.** Same `SCHEMA_v2 §2.4` rules apply, but the fix lives in a different framework integration. Split to [AI-621](https://linear.app/kurrent/issue/AI-621).
- **`extensions.openai.handoff.filtered_input`.** The subsession transcript IS the filtered view that the target agent actually saw; duplicating it doubles storage for no read benefit.

## Architecture

### Components

One new file plus edits to three existing ones in `openai-agents/python/kurrent_openai_agents/`:

**New:**

- `_handoffs.py` — pure logic, no I/O:
  - `derive_agent_id(target_name: str, call_id: str) -> str` — produces `sub-{slug(target_name)}-{call_id[-6:]}`, lowercased, safe-char-only per `SCHEMA_v2 §2.4`. The slug function lowercases the name, replaces any run of non-`[a-z0-9]` characters with a single `-`, and strips leading/trailing `-`.
  - `_HandoffLedger` dataclass — per-`KurrentDBSession` ledger: `expected: ExpectedHandoff | None`, `active: dict[str, ActiveHandoff]`, `current_owner: str` (stream name).
  - `route_items(items, ledger, *, session_id, parent_stream) -> list[RoutedSegment]` — splits a flat dict list into per-stream segments given the ledger's transitions. Pure function; trivially unit-testable.

**Edited:**

- `session.py` — `KurrentDBSession` now implements both `SessionABC` and `RunHooksBase`. Adds `_ledger` field, `on_handoff` / `on_agent_start` / `on_agent_end` overrides, and routes `add_items` through `route_items`. `get_items` re-flattens from parent + subsession streams.
- `_codec.py` — recognises `handoff_call` / `handoff_output` flat-dict shapes when the ledger has matching transitions; emits `SubagentStarted` / `SubagentCompleted` carrying `extensions.openai.raw_item` and `extensions.openai.handoff.*`. The today-OpenAIItem branch becomes a fallback for the hooks-not-wired case.
- `_stream_names.py` — adds `for_subsession(parent_session_id, agent_id) -> str` thin wrapper around `kurrent_agent_schema.agent_subsession_stream`.

### Why this shape

- **Pure logic in `_handoffs.py`** means the splitter and id derivation are testable without KurrentDB or `Runner`. Async I/O stays in `session.py`.
- **`KurrentDBSession` implements both interfaces.** User wires `Runner.run(..., session=s, hooks=s)`. Single object, no out-of-band ledger keyed by session_id, composable with the user's own hooks via standard `RunHooksBase` subclassing in user-land.
- **Graceful degradation.** If the user forgets `hooks=s`, the ledger stays empty, no handoff is recognised, and behavior matches today's `main` (handoffs as `OpenAIItem` on the parent stream). One WARN log line guides users to wire the hook.

## Write path

### Ledger state

`_HandoffLedger` carries three fields, all in-memory on the `KurrentDBSession`:

- `expected: ExpectedHandoff | None` — set by `on_handoff(from, to)`, consumed by the next matching `function_call` in `add_items`.
- `active: dict[call_id, ActiveHandoff]` — maps the SDK's `call_id` to `(agent_id, agent_type, subsession_stream)`.
- `current_owner: str` — stream the next non-handoff item routes to. Starts as parent stream; flips on handoff start / end.

### Hooks (`RunHooksBase` overrides on `KurrentDBSession`)

- `on_handoff(ctx, from_agent, to_agent)` — stash `ExpectedHandoff(from_name=from.name, to_name=to.name, to_agent=to)`. The `to_agent` reference lets us read `agent.name` and (in extensions) `agent.instructions`.
- `on_agent_end(ctx, agent, output)` — if `current_owner` still points at this agent's subsession (no explicit handoff back), emit a deferred `SubagentCompleted(outcome="success", summary=_serialize_output(output)[:512])` to both streams. Handles the "subagent produces final output without returning" case.

### `add_items(items)`

Walk items in order, maintaining `current_owner`:

1. **`function_call` AND `expected != None`** — recognised as the handoff call.
   - `call_id = item["call_id"]`; `agent_id = derive_agent_id(expected.to_name, call_id)`; `subsession_stream = agent_subsession_stream(session_id, agent_id)`.
   - Build `SubagentStarted` carrying:
     - `agent_id`, `agent_type = expected.to_name`, `prompt = item["arguments"]` (raw JSON string), `subsession_stream`, `timestamp`.
     - `extensions.openai.raw_item = item` (lossless parent view).
     - `extensions.openai.handoff.source_agent = expected.from_name`.
   - **Atomic dual-stream write** via `multi_append_to_stream([NewEvents(parent_stream, [evt]), NewEvents(subsession_stream, [evt])])`.
   - Register `active[call_id] = ActiveHandoff(agent_id, agent_type, subsession_stream)`; flip `current_owner` to `subsession_stream`; clear `expected`.

2. **`function_call_output` whose `call_id` is in `active`** — the handoff output.
   - Build `SubagentCompleted` carrying:
     - `agent_id = active[call_id].agent_id`, `outcome = "success"`, `summary = _serialize_output(item["output"])[:512]` (the existing `_codec` helper), `timestamp`.
     - `extensions.openai.raw_item = item`.
   - Atomic dual-stream write.
   - Remove from `active`; flip `current_owner` back to parent stream.

3. **Otherwise (regular item)** — run existing `items_to_canonical`, append events to `current_owner`.

### Multi-stream metadata constraint

`kurrentdbclient.v2streams._metadata_to_properties` (lines 87–107) requires multi-append event metadata to be **string-valued JSON only**. Single-stream `append_to_stream` accepts any JSON. The existing `_serialization.serialize` stamps `$schema_version` as int `2`.

Resolution: a new `serialize_for_multi_append(event, *, metadata) -> NewEvent` helper coerces `$schema_version` to string `"2"` and validates that no non-string values remain in the metadata dict. Only the lifecycle-event write path uses this helper; regular per-item appends keep the existing serializer (`$schema_version: 2` as int).

`$usage` metadata is not stamped on `SubagentStarted` / `SubagentCompleted` (no LLM call), so the string-only constraint doesn't conflict with any other field.

## Read path (`get_items`)

The SDK expects a flat `list[TResponseInputItem]` for replay. Re-flatten by interleaving subsession streams into the parent.

### Algorithm

1. Read parent `AgentSession-{session_id}` stream end-to-end.
2. Walk events in order. For each:
   - `SessionStarted` / `SessionEnded` / `SessionContinuedAs` → skip (lifecycle).
   - `SubagentStarted` (parent-stream copy):
     - Emit `extensions.openai.raw_item` dict (the original `handoff_call`).
     - Read the subsession stream from `subsession_stream` field. Cache result by stream name to avoid re-reads.
     - Skip mirrored `SubagentStarted` / `SubagentCompleted` from the subsession stream.
     - Run remaining subsession events through `canonical_to_items` and append.
   - `SubagentCompleted` (parent-stream copy):
     - Emit `extensions.openai.raw_item` dict (the original `handoff_output`).
   - Any other canonical event or `OpenAIItem`: run through `canonical_to_items` and append.
3. Apply caller's `limit` against the final flat list (`items[-limit:]`).

### Failure modes

- **Missing subsession stream.** Parent has `SubagentStarted` but the subsession stream doesn't exist (shouldn't happen under atomic dual-write; possible for streams from older non-atomic writers). Log WARN; emit just the `handoff_call` reconstruction without subagent items.
- **Subsession events from another framework.** No `extensions.openai.raw_item`. Fall through to the existing `_fallback_reconstruct` cross-framework path.

## Event field mappings

### `SubagentStarted`

| Schema field | Source | Notes |
|---|---|---|
| `agent_id` (req) | `f"sub-{slug(target.name)}-{call_id[-6:]}"` | Per `SCHEMA_v2 §2.4`. |
| `agent_type` | `target.name` | Human-readable role label. |
| `prompt` | `handoff_call.arguments` raw JSON string | The actual input passed to the subagent. |
| `subsession_stream` | `agent_subsession_stream(session_id, agent_id)` | Always populated. |
| `timestamp` | `datetime.now(UTC)` | |

### `SubagentCompleted`

| Schema field | Source | Notes |
|---|---|---|
| `agent_id` (req) | `active[call_id].agent_id` | Looked up from ledger. |
| `outcome` | `"success"` | OpenAI SDK doesn't expose a clean success/error boolean on `handoff_output`; default to `"success"`. Refinements (`"error"` from `_errors`-shaped payloads, `"cancelled"` from explicit cancellation) deferred until needed. |
| `summary` | First 512 chars of the existing `_serialize_output(item["output"])` (already used by `function_call_output` mapping in `_codec.py`) | Truncated to keep events small; full text preserved under `extensions.openai.raw_item`. |
| `timestamp` | `datetime.now(UTC)` | |

### `extensions.openai.*` shape

```json
{
  "openai": {
    "raw_item": { ...handoff_call or handoff_output dict... },
    "handoff": {
      "source_agent": "TriageAgent",
      "input_filter": "summarize_history"
    }
  }
}
```

- `raw_item` — as today, the lossless original dict. On `SubagentStarted` it's the `handoff_call`; on `SubagentCompleted` it's the `handoff_output`.
- `handoff.source_agent` — `from_agent.name` from `on_handoff`. Only on `SubagentStarted`. Useful for trace UIs and symmetric A → B → A handoffs.
- `handoff.input_filter` — name of the SDK's `Handoff.input_filter` function if applied, else absent. Captures the SCHEMA_v2 §3.5 concern about filtered history without re-recording the filtered transcript.

## Concurrency, edge cases, degradation

**Concurrency.** `Runner.run` is single-threaded per session. `StreamState.ANY` matches the v0 stance in `DESIGN.md §6`. Multi-stream `multi_append_to_stream` is atomic at KurrentDB layer — parent + subsession never desync.

**Duplicate hook delivery.** `expected` is set only when currently `None`; `active` is keyed by `call_id`. Duplicate `on_handoff` or duplicate `add_items` for the same `call_id` becomes a no-op on the second pass. Matches Capacitor's `TryMarkAgentActive` pattern (`SessionWriter.Writes.cs:678`).

**Subagent never returns.** Target produces final output without handing back. `on_agent_end` emits deferred `SubagentCompleted`.

**Crash mid-subagent.** Parent has `SubagentStarted` but no matching `SubagentCompleted`. `get_items` emits the `handoff_call` reconstruction, inlines whatever subagent items did land, stops. SDK sees an open turn and resumes; cross-framework readers treat subagent as "in progress."

**Hooks not wired.** `expected` always `None`. `handoff_call` / `handoff_output` items fall through to `OpenAIItem` (today's behavior). One-shot WARN on first such item logs the missing `hooks=session`.

**Nested handoffs.** First nested case logs WARN and degrades to `OpenAIItem`. Per schema's flat-only stance.

**Streaming mode.** `add_items` is called per checkpoint; `current_owner` persists across calls on the session object. Naturally streaming-safe.

**Session resume after process restart.** Ledger is per-process state, not persisted. SDK calls `get_items` to reconstruct the conversation; next handoff fires `on_handoff` fresh and the ledger repopulates. Same lifecycle as the `KurrentDBSession` object.

## Schema-doc changes (in this PR)

Three patches to `schema/SCHEMA_v2.md` land alongside the OpenAI Agents implementation:

### New §2.4 — Identifier conventions for stream names

```
### 2.4 Identifier conventions for stream names

All variable-substitution components of stream names (session_id, parent_session_id,
agent_id, app_name, user_id, scope, filename, run_id) MUST conform to the rules below.
The shared kurrent_agent_schema / Kurrent.Agent.Schema packages provide builders that
enforce these rules; producers SHOULD call those builders rather than concatenating strings.

Character set. ASCII [A-Za-z0-9._-]+, max 128 bytes per component. Producers MUST reject
or URL-encode anything outside that set.

GUID-shaped values. When a component value parses as a UUID/GUID, producers MUST emit it
in lowercase, dashless form (the .NET "N" format, e.g. 8d77fd28fda0485f9ae18ee9c7fc3751).
Readers MUST also accept the hyphenated "D" form as a legacy-compat fallback (matches
Capacitor's SessionStreamCandidates). Lowercase only — case-sensitivity differences
between writers would split a single conversation across two streams.

Non-GUID values. Used verbatim after the character-set check. Case-preserved.

Compound suffix separators. Where a stream name has two components joined by `-`
(e.g. AgentSubsession-{parent}-{agent_id}, AgentMemory-{app}-{user}), the separator is a
single `-`. Neither component may begin or end with `-`. Inner `-` characters within a
component are permitted (so agent_id = "sub-research-x9k2" is valid; consumers parse
right-to-left from the prefix to locate the component boundary).
```

### §3.5 — atomic dual-stream write

Change the `SubagentStarted` paragraph from "written to parent `AgentSession-` stream" to:

> Written **atomically to BOTH** the parent `AgentSession-` stream **and** the `AgentSubsession-` stream via a multi-stream append. This lets a reader landing on the subsession stream learn its lifecycle without joining back to the parent (Capacitor's trace-tree projector and per-agent eval queries depend on this).

Same for `SubagentCompleted`.

### §3.5 — `SubagentStarted` field notes

- `agent_id`: "Producer-chosen unique-per-invocation opaque string. Recommended shape: `{role_slug}-{short_unique}` for human-readable Capacitor UI."
- `agent_type`: "Producer-defined role/category string; opaque to canonical readers. Examples: `research`, `code-reviewer`, `general-purpose`, `TriageAgent`."

## Testing strategy

### Unit tests (no KurrentDB, no `Runner`)

New `tests/test_handoffs.py`:
- `derive_agent_id` produces the expected slug; collisions across `(target, call_id)` pairs.
- `_HandoffLedger` state machine — sequence of `on_handoff(...)` → `add_items([...])` calls produces expected routing.
- Splitter degradation — `handoff_call` without `expected` falls through; `handoff_output` without `active` falls through.

Extend `tests/test_codec.py`:
- `items_to_canonical` with handoff_call + handoff_output + ledger transitions emits `SubagentStarted` / `SubagentCompleted` carrying the right `extensions.openai.*`.
- `canonical_to_items` with mixed parent+subsession events rebuilds the flat dict list byte-for-byte.

### Integration tests (real KurrentDB via Testcontainers)

New `tests/test_handoff_session.py`:
- **End-to-end handoff happy path** — two `Agent`s, `Runner.run(agent_a, "...", session=s, hooks=s)`. Assert parent stream contents, subsession stream contents, and `get_items()` flat-replay equivalence.
- **One-way handoff (no return).** Assert `on_agent_end` triggers `SubagentCompleted(outcome="success", summary=...)`.
- **Hooks-not-wired degradation.** Assert `OpenAIItem` fallback, no `AgentSubsession-` stream, single WARN log.
- **Multi-stream metadata round-trip.** Append `SubagentStarted` via `multi_append_to_stream`; read back; assert `$schema_version == "2"` (string).
- **Duplicate `on_handoff` no-op.** Fire hook twice; assert exactly one `SubagentStarted` on the stream.

### Fixtures

Two integration-local fixtures under `openai-agents/python/tests/fixtures/`:
- `subagent_started_openai.json` — minimal `SubagentStarted` with `extensions.openai.raw_item` + `extensions.openai.handoff.source_agent`.
- `subagent_completed_openai.json` — same shape for `SubagentCompleted`.

Loaded by `test_codec.py`'s round-trip cases. Shared `schema/fixtures/events/Subagent*.json` keep their Claude-Code shape (those exercise the canonical contract; OpenAI extensions are integration-local).

### Updates

- `tests/test_session.py` — add regression for `get_items()` with `SubagentStarted` but no `AgentSubsession-` stream (simulated crash). Assert: emits `handoff_call` reconstruction, no subagent items, no exception.

### Sample

Update `openai-agents/python/samples/` with a triage-and-specialist sample (one handoff). Mirrors the SDK's own handoff sample so users can copy-paste.

## Out of scope / deferred

- **MAF .NET stream-name conformance** ([AI-621](https://linear.app/kurrent/issue/AI-621)).
- **Nested handoffs.** Schema's flat-only stance; first nested case degrades to `OpenAIItem` with a WARN.
- **`Runner.run_streamed` integration test.** State machine is streaming-safe by construction; dedicated streaming Testcontainers test deferred.
- **`outcome = "error"` / `"cancelled"`.** Detection of error / cancellation in `handoff_output` payloads. Default `"success"` until a concrete need surfaces.

## References

- `schema/SCHEMA_v2.md §3.5` — canonical subagent shape.
- `openai-agents/python/DESIGN.md §4` — handoff catalogue.
- `openai-agents/python/DESIGN.md §8 Q3` — the AI-475 deferral note.
- `kapacitor-server/src/Kurrent.Capacitor/Sessions/SessionWriter.Writes.cs:676-728` — Capacitor's dual-stream lifecycle pattern.
- `kapacitor-server/src/Kurrent.Capacitor/Evals/EventStoreReadExtensions.cs:14-29` — Capacitor's `NormalizeId` + `SessionStreamCandidates`.
- `openai-agents/python/.venv/lib/python3.11/site-packages/agents/lifecycle.py` — SDK's `RunHooksBase` extension point.
- `openai-agents/python/.venv/lib/python3.11/site-packages/agents/items.py:265-310` — `HandoffCallItem` / `HandoffOutputItem`.
