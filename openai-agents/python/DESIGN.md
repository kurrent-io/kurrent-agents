# Kurrent OpenAI Agents (Python) — Design

KurrentDB integration for the [OpenAI Agents SDK (Python)](https://github.com/openai/openai-agents-python). Shares the canonical event schema with the other integrations in this monorepo (see [`../../schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md)).

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

| OpenAI item `type` | Canonical event(s) | Notes |
|---|---|---|
| `message` (role=user) | `UserMessageReceived` | |
| `message` (role=assistant) | `AssistantTextGenerated` | |
| `function_call` | `AssistantToolCallsGenerated` (one tool_call per event) | |
| `function_call_output` | `ToolResultReceived` | |
| `reasoning` | `AssistantThinkingGenerated` | Plaintext content, or `encrypted=true` + `signature` for o-series; opaque blob in `extensions.openai.thinking.raw`. SCHEMA_v2 §3.2. |
| `mcp_approval_request` | `InterruptIssued` (`kind="approval"`) | Proposed call under `extensions.openai.interrupt.proposed_call`; post-hoc gate (`request_id == call_id`). SCHEMA_v2 §3.3. |
| `mcp_approval_response` | `InterruptResolved` | `outcome=allow|deny` from `approve`; `response` from `reason`. |
| `handoff_call` | `SubagentStarted` (when hooks=session) | else canonical `AssistantToolCallsGenerated` on the parent stream. `agent_id` derived as `sub-{slug(target.name)}-{call_id[-6:]}` per SCHEMA_v2 §2.4. Original dict preserved under `extensions.openai.raw_item`. |
| `handoff_output` | `ToolResultReceived` on the **subsession** stream (when hooks=session) | else canonical `ToolResultReceived` on the parent stream. The synthetic transfer-ack is part of the handoff setup, not its close. Original dict preserved under `extensions.openai.raw_item`. |
| (no SDK item — fires from `on_agent_end`) | `SubagentCompleted` (when hooks=session) | Emitted at the end of the next `add_items` after the target produces its final output, so the closing marker lands AFTER the target's last message. Synthetic event — no underlying SDK item, so no `extensions.openai.raw_item`. |
| `computer_call`, `shell_call`, `web_search`, … | `OpenAIItem` | No canonical analogue. |

**Canonical events derived from an SDK item stash that item under `extensions.openai.raw_item`** — lossless reconstruction regardless of which branch it took. `SubagentCompleted` is the one exception: it has no corresponding SDK item (it's emitted by the `on_agent_end` lifecycle hook), so it carries no `raw_item`.

### Read path (`get_items`)

For each event on the parent stream:
- `SubagentStarted` → emit the original `handoff_call` dict from `extensions.openai.raw_item`, then **inline the subsession transcript** by reading the stream referenced in `subsession_stream` (per-`get_items` cache prevents re-reads).
- `SubagentCompleted` → skip. It's a lifecycle-only marker with no flat-list correlate; the SDK's view ends with the target's last regular item from the subsession transcript.
- Any other canonical event → if `extensions.openai.raw_item` is present, return that dict verbatim (preferred — lossless). Otherwise (cross-framework read — e.g. a session written by an ADK agent, read by the OpenAI SDK), rebuild a minimal Responses API item from canonical fields. Good enough for the SDK to append to and continue.

`SessionStarted` and `SessionEnded` are framework-level lifecycle markers; not surfaced to `get_items`.

## 3.5 Schema dependency

The integration depends on the shared `kurrent-agent-schema` Python package
(see [`../../schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md)). Canonical
event types are protobuf messages (Edition 2024); the integration imports
them directly from `kurrent_agent_schema`. Stream-name builders and the
`$usage` metadata key come from the same package.

The framework-specific `OpenAIItem` event remains a local Pydantic model in
`_openai_events.py` because it is not part of the cross-framework contract.

JSON wire format goes through the schema package's `to_json` / `from_json`
helpers exclusively — direct calls to `google.protobuf.json_format` are not
supported on this code path. Each event's metadata carries
`$schema_version = 2` per SCHEMA_v2 §9.

## 4. OpenAI-specific concepts

- **Handoffs** — `handoff_call` and `handoff_output` items. LLM-driven nested agent invocation. Promoted to canonical lifecycle per SCHEMA_v2 §3.5 when `Runner.run(..., session=s, hooks=s)` is wired (the session itself implements `RunHooksBase`). The promotion is driven by three lifecycle hooks:
  - **`on_llm_end(agent, response)`** — inspects `response.output` for function calls whose name appears in the agent's registered handoff tool map; stashes a `PendingHandoffCall(tool_name, call_id)` per match. The list is rebuilt per LLM response so unchosen candidates don't drift forward.
  - **`on_handoff(from, to)`** — pops the matching `PendingHandoffCall` and copies its exact `(tool_name, call_id)` into the ledger's `ExpectedHandoff`. `route_items` then promotes the matching `handoff_call` to `SubagentStarted` (atomic dual-stream write to parent + the new `AgentSubsession-{session_id}-{agent_id}` stream). The `handoff_output` (synthetic ack from the SDK) flows through as `ToolResultReceived` on the subsession — it's part of the handoff *setup*, not its close. Target-agent items routed to the subsession until close.
  - **`on_agent_end(target, output)`** — defers a `SubagentCompleted` via `pending_close`; the close lands at the end of the next `add_items` so the target's final message persists on the subsession BEFORE the lifecycle marker.

  Without `hooks=s` wired, handoffs still persist (the tool call surfaces as canonical `AssistantToolCallsGenerated` / `ToolResultReceived` on the parent stream), but the `SubagentStarted` / `SubagentCompleted` lifecycle and the dedicated subsession transcript stream are not emitted — cross-framework readers won't see the subagent boundary. See AI-471.
- **Guardrails** — runtime checks, not session items; nothing for us to persist.
- **MCP approvals** — `mcp_approval_request` / `mcp_approval_response` decompose into canonical `InterruptIssued` / `InterruptResolved` (`kind="approval"`). Proposed call rides under `extensions.openai.interrupt.proposed_call`; post-hoc gate (request_id == call_id) per SCHEMA_v2 §3.3.
- **Computer / shell tools** — `computer_call`, `shell_call`. First-class tool types specific to OpenAI's sandbox extensions; ride as `OpenAIItem`.
- **Reasoning** — `reasoning` items decompose into canonical `AssistantThinkingGenerated` per SCHEMA_v2 §3.2. Plaintext content rides on the canonical event; o-series encrypted blobs ride under `extensions.openai.thinking.raw` with `encrypted=true` and `signature` populated on the canonical event.
- **Structured output** — `AgentOutputSchema` / parsed Pydantic payloads. Not a session item; carried on `RunResult` alongside session history.

## 5. Stream layout

```
AgentSession-{session_id}      conversation items (primary write path)
AgentMemory-{app}-{user}       planned — cross-session memory (§7)
```

`session_id` is opaque; `app_name` and `user_id` are constructor configuration on `KurrentDBSession` (the SDK has no native app/user concept). See `SCHEMA_v2.md §3.1` (carried unchanged from SCHEMA.md §5.3).

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
3. ~~**Handoff visibility.**~~ ✅ Resolved AI-471. Promotion via `RunHooksBase` on `KurrentDBSession` itself; the parent's `SubagentStarted` carries the original `handoff_call` dict under `extensions.openai.raw_item` so OpenAI's flat-replay invariant is preserved. Subsession streams are atomically dual-written for self-describing reads. Nested handoffs deliberately deferred per the schema's flat-only stance.
