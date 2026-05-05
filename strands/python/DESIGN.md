# Kurrent Strands (Python) — Design

KurrentDB integration for [Strands Agents (Python)](https://github.com/strands-agents/sdk-python). Shares the canonical event schema (v2) with the Google ADK and Microsoft Agent Framework integrations in this monorepo (see [`../../schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md) and the shared `kurrent-agent-schema` package under [`../../schema/python/`](../../schema/python/)).

> **Revision note.** Migrated to canonical schema v2: replaced the hand-rolled `_schema/` event models with the shared `kurrent-agent-schema` package, promoted reasoning content to canonical `AssistantThinkingGenerated`, and started emitting canonical `InterruptIssued` / `InterruptResolved` for tool-approval pauses (see DEV-1529).

## 1. Goal

Provide a pip-installable `kurrent-strands` package that lets any Strands `Agent` persist its conversation to KurrentDB by passing a `KurrentDBSessionManager` as the agent's `session_manager`. No other code changes needed.

## 2. Strands plug-point we use

Strands exposes two ways to integrate persistence:

- `SessionRepository` — CRUD-shaped repository (used by the bundled `FileSessionManager` / `S3SessionManager`).
- `SessionManager` (abstract) — full control over persistence. Registers hooks on `AgentInitializedEvent`, `MessageAddedEvent`, `AfterInvocationEvent`, plus multi-agent/bidi variants.

We implement **`SessionManager`** directly. The repository pattern is built for message-index CRUD; our event-sourced writes don't map cleanly onto `read_message(index)`. A direct `SessionManager` subclass gives us full control over the canonical event emission.

**Sync, not async.** `SessionManager` callbacks are invoked synchronously by Strands hooks, so this integration uses the sync `KurrentDBClient`. The ADK integration in this monorepo uses `AsyncKurrentDBClient` because ADK is async-native.

## 3. Integration style — snapshot-to-events on each turn

Strands is not event-sourced natively; it hands us whole `Message` objects as they're appended. Our write path:

- **`initialize(agent)`** — read `AgentSession-{session_id}`. Reconstruct `Message` objects from canonical events and assign to `agent.messages`. Restore `agent.conversation_manager` state from the latest framework-specific `StrandsAgentState` event.
- **`append_message(message, agent)`** — decompose the `Message` into canonical events (see §4) and append. Attach `$usage` KurrentDB event metadata when the message carries `metadata.usage`.
- **`sync_agent(agent)`** — emit a `StrandsAgentState` framework-specific event carrying `state`, `conversation_manager_state`, and `_internal_state` (interrupt + model state) from the agent.
- **`redact_latest_message(redact_message, agent)`** — emit a `MessageRedacted` framework-specific event pointing at the previous event.

## 4. Canonical mapping — Strands `Message` → canonical events

| Strands message shape | Canonical event |
|---|---|
| `role=user`, text `ContentBlock`s | `UserMessageReceived` |
| `role=assistant`, text only | `AssistantTextGenerated` |
| `role=assistant`, `toolUse` block(s) (± text) | `AssistantToolCallsGenerated` |
| `role=user`, `toolResult` block(s) | one `ToolResultReceived` per result |
| `role=assistant`, `reasoningContent` block(s) | `AssistantThinkingGenerated` (one per block, emitted before the text/tool event for the same turn) |
| `role=user`, rich content (image/document/video) | `UserMessageReceived` with `extensions.strands.non_canonical_blocks` carrying the full block list |
| `role=assistant`, `citationsContent`, cache points, etc. | text/tool event + `extensions.strands.non_canonical_blocks` carries the rich blocks |

Reasoning content is now canonical (`SCHEMA_v2.md §3.2`). The `reasoningText.text` rides in `AssistantThinkingGenerated.content`; the optional `reasoningText.signature` rides in the canonical `signature` field; `redactedContent` bytes ride as base64 in `extensions.strands.thinking.redacted_content` (the canonical event has no field for opaque vendor blobs). Strands reasoning is plaintext, so `encrypted` stays at the proto default of `false`.

Tool-approval pauses (Strands' `Interrupt`) emit canonical `InterruptIssued` / `InterruptResolved` per `SCHEMA_v2.md §3.3`. Classification is **post-hoc**: the interrupt raises at the `BeforeToolCallEvent` hook with `event.tool_use["toolUseId"]` already populated, so the canonical `request_id` is the eventual `call_id` (no synthetic GUID). Runtime-only `_internal_state.interrupt_state` continues to round-trip via `StrandsAgentState` so the agent can resume mid-turn.

Non-canonical content (images, documents, citations, cache points, custom metadata) rides in `extensions.strands` on each emitted event. A same-framework reader reconstructs the full `Message`; a cross-framework reader (ADK / AFW) sees plain text, tool interactions, thinking, and interrupts.

**Token usage** attaches as the `$usage` KurrentDB event metadata per canonical shape (`SCHEMA_v2.md §3.6`), with the field-name shim: Strands' `Usage.inputTokens` → `input_tokens`, `outputTokens` → `output_tokens`, `cacheReadInputTokens` → `cached_input_tokens`, `cacheWriteInputTokens` → `cache_write_input_tokens`. Strands' `Usage` does not surface `reasoning_tokens` (folded into `outputTokens` upstream), so canonical `$usage.reasoning_tokens` stays absent on Strands-emitted events.

## 5. Framework-specific events in the session stream

These live in `AgentSession-{session_id}` alongside canonical events. Non-owning frameworks (ADK / AFW) ignore them on read; Strands uses them to rebuild agent state. The `extensions.strands` slug is reserved for the Strands integration (per `SCHEMA_v2.md §5`) and carries non-canonical content blocks plus per-event payload extras (e.g. `tool_result.status`, `thinking.redacted_content`).

| Event type | Purpose |
|---|---|
| `StrandsAgentState` | Serialised `SessionAgent` — `state`, `conversation_manager_state`, `_internal_state` (including `interrupt_state` for resume). Emitted on every `sync_agent`. Readers take the latest. Stays framework-specific (not canonical). |
| `MessageRedacted` | Marks a prior message as redacted; carries the new content and a pointer (by canonical event id or invocation id) to the original. |

## 6. Stream layout

Same canonical scheme as the other integrations (see `SCHEMA_v2.md §2.1` for the shared-stream table):

```
AgentSession-{session_id}      conversation + Strands state events (primary write path)
AgentMemory-{app_name}-{user_id}   retained facts, per-app per-user (KurrentDBAgentMemory)
```

Strands' write path only touches `AgentSession-{session_id}`; `AgentMemory-{app_name}-{user_id}` is owned by `KurrentDBAgentMemory` for cross-session fact recall.

`{session_id}` is opaque; Strands supplies it at `KurrentDBSessionManager(session_id=...)` construction. `{app_name}` and `{user_id}` are also explicit constructor args — Strands itself has no `app_name` concept, so the integration takes them as configuration (cross-cutting convention; see the monorepo `CLAUDE.md`).

## 7. Coverage and what's deferred

**Emitted today.**

- All canonical conversational events (`UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived`).
- Canonical reasoning (`AssistantThinkingGenerated`, `SCHEMA_v2.md §3.2`) — `reasoningContent` blocks emit one thinking event per block, ahead of the text/tool event for the same turn.
- Canonical tool-approval pauses (`InterruptIssued` / `InterruptResolved`, `SCHEMA_v2.md §3.3`) — Strands' `Interrupt` raises post-hoc at `BeforeToolCallEvent`, so canonical `request_id` is the eventual `call_id`. The `_internal_state.interrupt_state` round-trip via `StrandsAgentState` continues alongside, so the agent can resume mid-turn.
- Memory (`KurrentDBAgentMemory`) — cross-session fact recall against `AgentMemory-{app}-{user}`.

**Deferred (not yet emitted).**

- **Multi-agent / subagents** (`SCHEMA_v2.md §3.5`, `AgentSubsession-{parent}-{agent_id}`, `SubagentStarted` / `SubagentCompleted`). `sync_multi_agent` / `initialize_multi_agent` stay abstract/unimplemented; Swarm and Graph orchestrator state will map onto the canonical subagent surface when Strands multi-agent persistence lands. Until then, Strands' `multiagent_*` events are not persisted.
- **BidiAgent** — bidi streaming agent (experimental in Strands) stays unimplemented; its hooks raise `NotImplementedError` inherited from the base class.
- **Session chaining** (`previous_session_id` / `SessionContinuedAs`, `SCHEMA_v2.md §3.1`). Strands has no native resume / fork concept; the constructor-supplied `session_id` is opaque. Out of scope until a use case surfaces.
- **Artifacts** — Strands core has no artifact service. The canonical `ArtifactVersionCreated` shape (`SCHEMA_v2.md §3.7`) is consumed when present from cross-framework writes but not produced.
- **Memory / LoadMemory tool equivalent** — ADK ships `load_memory_tool`; Strands doesn't. Deferred.

## 8. Open questions

1. **TS SDK alignment.** The canonical JSON is snake_case; Strands TS uses camelCase. Deferred — see `SCHEMA_v2.md` and the Strands integration project doc in Linear.
2. **Session-scoping semantics.** Strands' `session_id` is a flat string; we interpret it as the canonical `session_id` on the stream. Multi-tenant apps should make it globally unique (e.g. `f"{app}_{user}_{local_id}"`).
3. **Conversation-manager state in `extensions.strands.conversation_manager` vs. its own event?** Current plan: its own event (`StrandsAgentState`), since it needs to be findable and snapshot-style. Revisit once we add `SummarizingConversationManager` support.
