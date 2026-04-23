# CLAUDE.md

Guidance for agents working in this repo. Start here; follow the links for vendor-specific depth.

## What this repo is

A monorepo of KurrentDB integrations for AI agent SDKs. Every integration writes to the **same canonical event schema**, so a session produced by one framework is readable by another.

**Source of truth for the schema:** [`schema/SCHEMA.md`](./schema/SCHEMA.md). Read before writing any code that emits or consumes events. Field names, stream naming (`AgentSession-{session_id}`, `AgentMemory-{app}-{user}`), `$usage` metadata shim rules, and `extensions.<framework>` conventions all live there.

## Per-integration design docs (progressive disclosure)

Each package has its own `DESIGN.md` with the full spec — plug-points, storage strategy, concurrency, open questions, known limits. Read these only when touching that integration.

| Framework | DESIGN doc | Storage style |
|---|---|---|
| Google ADK (Python) | [`google-adk/python/DESIGN.md`](./google-adk/python/DESIGN.md) | verbatim `Event` with state-scope routing (app / user / session streams) |
| MS Agent Framework (.NET) | [`microsoft-agent-framework/dotnet/README.md`](./microsoft-agent-framework/dotnet/README.md) | typed canonical events via shared `Kurrent.Agent.Schema` (.NET); MAF-specific fields under `extensions.afw` |
| MS Agent Framework (Python) | [`microsoft-agent-framework/python/README.md`](./microsoft-agent-framework/python/README.md) | typed canonical events via shared `kurrent-agent-schema` (Python); MAF-specific fields under `extensions.afw`; canonical-payload parity with MAF .NET (structural, not raw-byte) |
| Strands (Python) | [`strands/python/DESIGN.md`](./strands/python/DESIGN.md) | snapshot-to-events on each turn; Strands-specific state in `extensions.strands` + `StrandsAgentState` event |
| OpenAI Agents (Python) | [`openai-agents/python/DESIGN.md`](./openai-agents/python/DESIGN.md) | decompose items to canonical events; full original dict in `extensions.openai.raw_item` for lossless round-trip; non-canonical items wrap as `OpenAIItem` |
| Claude Agent SDK (Python) | [`claude-agent-sdk/python/DESIGN.md`](./claude-agent-sdk/python/DESIGN.md) | **verbatim-only** mirror of opaque CLI entries (`ClaudeSDKEntry`) on schema v2 via shared `kurrent-agent-schema`; local disk remains source of truth. Read-side decomposer for canonical reads lives in the same package. |

## Cross-cutting conventions

- **Canonical events are lingua franca.** `UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived` must match `SCHEMA.md §3` exactly. Framework-specific shapes go in `extensions.<framework>.*` or in a distinct event type (`OpenAIItem`, `ClaudeSDKEntry`, `StrandsAgentState`, …).
- **`app_name` / `user_id` are constructor kwargs.** SDKs that don't have these concepts (Strands, OpenAI Agents, Claude SDK) take them as explicit configuration. See `SCHEMA.md §5.3`.
- **Token usage rides on `$usage` KurrentDB event metadata**, not in payload. Field-name shims (e.g. Strands' `inputTokens` → canonical `input_tokens`) live in each integration's write path.
- **Sync vs async clients.** ADK uses `AsyncKurrentDBClient` (ADK is async-native). Strands uses sync `KurrentDBClient` (its `SessionManager` hooks are sync). Follow the upstream SDK's style.
- **Concurrency.** Only ADK implements optimistic-concurrency today (last-seen-revision + one retry on `WrongExpectedVersion`). Other integrations use `StreamState.ANY` until a concrete contention case appears.

## Gotchas worth knowing before editing

- **Claude SDK `SessionStore` optional methods must be *absent*, not raise.** The SDK duck-types presence via `hasattr`. Defining-but-raising `list_sessions`/`delete`/`list_subkeys` breaks resume. See commit `6a33c21`.
- **ADK `get_session` must re-hydrate `Event.usage_metadata`.** It lives on `LlmResponse`, not in our canonical payload. Missing this silently loses token counts. See commit `de2c3eb` (DEV-1479).
- **ADK codec handles empty-args tool calls.** Pydantic serialisation drops empty dicts; the codec must preserve them for round-trip. See commit `ff1540d`.
- **Strands tool-result status round-trips via `extensions.strands`.** Canonical `ToolResultReceived` has no status field. See commit `2080f7a`.
- **MAF Python depends on `agent-framework-core`, not the `agent-framework` meta-package.** The meta-package pulls in `agent-framework-azure-ai-search==0.0.0a1`, a placeholder whose 0-byte `agent_framework/__init__.py` clobbers the real re-exports during install and breaks every top-level import (`Content`, `Message`, `HistoryProvider`, …). See DEV-1495.

## When to promote something to this file

Add a new entry here when:
- A *new integration* lands (new row in the DESIGN-docs table).
- A *cross-cutting convention* changes (new canonical field, stream-naming rule, usage-metadata shape).
- A *non-obvious gotcha* would otherwise cost the next agent a debugging session.

Keep this file short. Depth belongs in `SCHEMA.md` and per-package `DESIGN.md`.
