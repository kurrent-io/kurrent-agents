# Kurrent OpenAI Agents (Python) — Design

KurrentDB integration for the [OpenAI Agents SDK (Python)](https://github.com/openai/openai-agents-python). Shares the canonical event schema with the other integrations in this monorepo (see [`../../schema/SCHEMA.md`](../../schema/SCHEMA.md)).

## 1. Goal

Provide a pip-installable `kurrent-openai-agents` package with a `KurrentDBSession(SessionABC)` class that drops into `Runner(..., session=...)` and persists conversation history to KurrentDB. Zero changes to user agent code.

## 2. SDK plug-point we use

The OpenAI Agents SDK ships a `Session` protocol (`src/agents/memory/session.py:14`) + `SessionABC` abstract base with four async methods:

- `get_items(limit) -> list[TResponseInputItem]`
- `add_items(items) -> None`
- `pop_item() -> TResponseInputItem | None`
- `clear_session() -> None`

We subclass `SessionABC`. The SDK is persistence-agnostic — it owns streaming, tool orchestration, and handoff routing; all durable state is delegated to the `Session` implementation. This is the cleanest integration surface we've seen across the seven SDKs audited.

## 3. Integration style — decompose-on-append, reconstruct-on-get

OpenAI session items are Responses API dicts with a discriminated `type` field (`message`, `function_call`, `function_call_output`, `reasoning`, `handoff_call`, `mcp_approval_request`, `computer_call`, `shell_call`, …).

### Write path (`add_items`)

Each item maps to one event:

| OpenAI item `type` | Canonical event |
|---|---|
| `message` (role=user) | `UserMessageReceived` |
| `message` (role=assistant) | `AssistantTextGenerated` |
| `function_call` | `AssistantToolCallsGenerated` (one tool_call per event) |
| `function_call_output` | `ToolResultReceived` |
| everything else | `OpenAIItem` (framework-specific; carries the raw dict verbatim) |

**Every emitted event also stashes the full original item under `extensions.openai.raw_item`** — lossless reconstruction regardless of which branch it took. Items that don't map onto canonical conversation events ride purely in `OpenAIItem`.

### Read path (`get_items`)

For each event:
- If `extensions.openai.raw_item` is present, return that dict verbatim (preferred — lossless).
- Otherwise (cross-framework read — e.g. a session written by an ADK agent, read by the OpenAI SDK), rebuild a minimal Responses API item from canonical fields. Good enough for the SDK to append to and continue.

`SessionStarted` and `SessionEnded` are framework-level lifecycle markers; not surfaced to `get_items`.

## 4. OpenAI-specific concepts

These ride in `extensions.openai.raw_item` or as `OpenAIItem` events; a cross-framework reader ignores them safely.

- **Handoffs** — `handoff_call` and `handoff_output` items. LLM-driven nested agent invocation with optional history filtering / compression.
- **Guardrails** — input/output/tool guardrails are runtime checks, not session items; they don't land in session history at all. If a guardrail tripwire fires, it halts execution before `add_items` — there's nothing for us to persist.
- **MCP approvals** — `mcp_approval_request` / `mcp_approval_response`. Optional human-in-the-loop checkpoints on MCP-backed tools.
- **Computer / shell tools** — `computer_call`, `shell_call`. First-class tool types specific to OpenAI's sandbox extensions.
- **Reasoning** — `reasoning` items (Responses API). Internal chain-of-thought traces returned by some models.
- **Structured output** — `AgentOutputSchema` / parsed Pydantic payloads. Not a session item; carried on `RunResult` alongside session history.

## 5. Stream layout

```
AgentSession-{session_id}      conversation items (primary write path)
AgentMemory-{app}-{user}       planned — cross-session memory (§7)
```

`session_id` is opaque; `app_name` and `user_id` are constructor configuration on `KurrentDBSession` (the SDK has no native app/user concept). See `SCHEMA.md §5.3`.

## 6. Concurrency

SDK consumers call `add_items` serially during a run (one append per turn, not per micro-event). v0 uses `StreamState.ANY` appends — no conflict handling. A later revision can add optimistic-concurrency tracking like the ADK integration, once we have a concrete use case (two OpenAI agents racing on the same `session_id` is unusual).

## 7. Out of scope for v0

- **Memory** — `KurrentDBAgentMemory` parallel to the ADK / Strands classes. Would hook into user-defined tools calling a `remember` / `recall_memory` pair.
- **Artifacts** — no SDK-level artifact concept. Would require callers to wire their own storage tools.
- **Token usage on events** — the SDK aggregates `Usage` at the run level (`RunResult.usage`), not per-message. v0 doesn't attach `$usage` to individual events; a follow-up can extend the write path to accept optional per-item usage.
- **`pop_item` tombstoning** — v0 returns the last item but doesn't remove it from the stream. Append-only storage means proper "pop" needs a tombstone marker readers can interpret; deferred until a concrete caller needs it.
- **`OpenAIConversationsSession` / `OpenAIResponsesCompactionSession` parity** — these are OpenAI-server-managed session decorators. A Kurrent-backed session deliberately opts out of those server-side features.
- **Structured output round-trip** — `RunResult.structured_output` lives outside the session stream. If a user wants it persisted, they call `add_items` themselves with the final item.

## 8. Open questions

1. **`pop_item` semantics** — the SDK uses it for the compaction path (`OpenAIResponsesCompactionSession`). Need a concrete "pop" definition (tombstone marker event? Rewind-style marker?). Probably won't block v1 since most users don't exercise this path directly.
2. **Per-turn usage capture** — add a callback/hook so the caller can attach `$usage` to specific items? Or extend `add_items` with a parallel `usage_per_item` argument? Revisit once there's a concrete need.
3. **Handoff visibility** — `HandoffCallItem` and `HandoffOutputItem` are durable session items, so they land as `OpenAIItem` events. Should they additionally emit canonical `AgentTransferred`-style events for cross-framework observability? Decision: no in v0 — multi-agent canonical events aren't standardised (`SCHEMA.md §3.3`).
