# Kurrent Strands (Python) — Design

KurrentDB integration for [Strands Agents (Python)](https://github.com/strands-agents/sdk-python). Shares the canonical event schema with the Google ADK and Microsoft Agent Framework integrations in this monorepo (see [`../../schema/SCHEMA.md`](../../schema/SCHEMA.md)).

> **Revision note.** v0: scaffolding + core `SessionManager` methods working against KurrentDB.

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
| `role=user`, rich content (image/document/video) | `UserMessageReceived` with extensions carrying the full block list |
| `role=assistant`, `reasoningContent`, `citationsContent` | text event + extensions carry the rich blocks |

Non-canonical content (images, documents, reasoning, citations, cache points, custom metadata) rides in `extensions.strands` on each emitted event. A same-framework reader reconstructs the full `Message`; a cross-framework reader (ADK / AFW) sees plain text and tool interactions.

**Token usage** attaches as the `$usage` KurrentDB event metadata per canonical shape (`SCHEMA.md` §3.4), with the field-name shim: Strands' `Usage.inputTokens` → `input_tokens`, `outputTokens` → `output_tokens`, `cacheReadInputTokens` → `cached_input_tokens`, etc.

## 5. Framework-specific events in the session stream

These live in `AgentSession-{session_id}` alongside canonical events. Non-owning frameworks (ADK / AFW) ignore them on read; Strands uses them to rebuild agent state.

| Event type | Purpose |
|---|---|
| `StrandsAgentState` | Serialised `SessionAgent` — `state`, `conversation_manager_state`, `_internal_state`. Emitted on every `sync_agent`. Readers take the latest. |
| `MessageRedacted` | Marks a prior message as redacted; carries the new content and a pointer (by canonical event id or invocation id) to the original. |

## 6. Stream layout

Same canonical scheme as the other integrations:

```
AgentSession-{session_id}      conversation + Strands state events (primary write path)
AgentMemory-{app}-{user}       planned — memory retention (§7)
```

`{session_id}` is opaque; Strands supplies it at `KurrentDBSessionManager(session_id=...)` construction. `{app_name}` and `{user_id}` are also explicit constructor args — Strands itself has no `app_name` concept, so the integration takes them as configuration (see `SCHEMA.md §5.3`).

## 7. Out of scope for v0

- **Memory** — no `KurrentDBMemoryManager` yet. Strands core ships without a memory abstraction; a v1 can add one modelled on ADK's.
- **Artifacts** — same; Strands core has no artifact service.
- **Multi-agent** — `sync_multi_agent` / `initialize_multi_agent` stay abstract/unimplemented. Swarm/Graph orchestrator state goes in `extensions.strands.multiagent` when we implement it.
- **BidiAgent** — bidi streaming agent (experimental in Strands) also stays unimplemented; its hooks raise `NotImplementedError` inherited from the base class.
- **Interrupts** — tracked on `SessionAgent._internal_state.interrupt_state`; persisted via `StrandsAgentState` round-trip. The canonical `InterruptIssued` / `InterruptResolved` event types (reserved in `SCHEMA.md §3.8`) are not yet emitted.
- **Memory / LoadMemory tool equivalent** — ADK ships `load_memory_tool`; Strands doesn't. Deferred.

## 8. Open questions

1. **TS SDK alignment.** The canonical JSON is snake_case; Strands TS uses camelCase. Deferred — see `SCHEMA.md §8` and the Strands integration project doc in Linear.
2. **Session-scoping semantics.** Strands' `session_id` is a flat string; we interpret it as the canonical `session_id` on the stream. Multi-tenant apps should make it globally unique (e.g. `f"{app}_{user}_{local_id}"`).
3. **Conversation-manager state in `extensions.strands.conversation_manager` vs. its own event?** Current plan: its own event (`StrandsAgentState`), since it needs to be findable and snapshot-style. Revisit once we add `SummarizingConversationManager` support.
