# Kurrent Claude Agent SDK (Python) — Design

KurrentDB `SessionStore` adapter for the [Claude Agent SDK (Python)](https://github.com/anthropics/claude-agent-sdk-python). Shares the canonical event schema with the rest of the monorepo — see [`../../schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md). Canonical events (`SessionStarted`, `UserMessageReceived`, `AssistantTextGenerated`, `AssistantThinkingGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived`) and stream-name helpers come from the shared [`kurrent-agent-schema`](../../schema/python/) package.

## 1. Goal

A pip-installable `kurrent-claude-agent-sdk` package exposing `KurrentDBSessionStore`. Wired via `ClaudeAgentOptions(session_store=...)`, it mirrors every transcript entry the Claude Code CLI writes locally to a KurrentDB stream, and reconstructs entries on `--resume`.

## 2. Why this SDK is architecturally different

The other integrations in this monorepo target frameworks that own their own conversation state at the Python layer. The Claude Agent SDK is a **subprocess wrapper**: the Python code spawns the Claude Code CLI, the CLI writes JSONL transcripts to `CLAUDE_CONFIG_DIR`, and the Python SDK receives parsed `Message` objects as the CLI emits them. The SDK is stateless.

The integration point is the `SessionStore` protocol (`src/claude_agent_sdk/types.py:1169`) — an adapter that receives a **secondary copy** of every JSONL line after the local disk write succeeds. This is deliberately post-hoc: local durability is the source of truth, the adapter is for mirroring.

Consequence: `KurrentDBSessionStore` is a thin streaming mirror, not a full session service. It does not own conversation state at runtime. The write path is **verbatim-only** — each JSONL line is wrapped in one `ClaudeSDKEntry` event and appended unchanged.

## 3. Contract

The SDK's `SessionStore` protocol has two required methods and four optional:

| Method | Required? | Our implementation |
|---|---|---|
| `append(key, entries)` | ✅ | Wrap each entry as a `ClaudeSDKEntry` event; append to `AgentSession-{session_id}` for the main transcript or `AgentSubsession-{session_id}-{agent_id}` for subagents (see §5). Exceptions are logged — subprocess keeps running per SDK contract. |
| `load(key)` | ✅ | Read every `ClaudeSDKEntry` from the stream for `key` (same naming rule as `append`); return `entry.raw_entry` dicts in stream order. Returns `None` when the stream is missing or has no `ClaudeSDKEntry` entries matching the requested scope. |
| `list_sessions(project_key)` | Optional | **Absent** — the SDK probes via `hasattr` and skips when missing. A future version can wire this to the `$ce-AgentSession` category projection. |
| `list_session_summaries(project_key)` | Optional | **Absent.** Added in SDK 0.1.65 for summaries incrementally maintained inside `append()`. Implementing it means giving up at-most-once / fire-and-forget semantics, so deferred. |
| `delete(key)` | Optional | **Absent** — no-op per SDK contract for append-only stores. A tombstone-marker event could fulfil this later without breaking the append-only invariant. |
| `list_subkeys(key)` | Optional | **Absent** — main transcript only. A follow-up can enumerate `AgentSubsession-{session_id}-*` by category projection. |

> The optional methods must be **absent from the class**, not defined-but-raising. The SDK duck-types presence with `hasattr`; a defined-but-raising method is still "present" and surfaces the error instead of falling back. See commit `6a33c21`.

## 4. Entries are stored verbatim; canonical view comes from a read-side projection

The SDK documents that `SessionStoreEntry` is a discriminated union whose concrete shape is internal and unstable, and that the only guaranteed invariant is **`load(append(entries)) == entries`** (deep-equal, not byte-equal). We respect that: every entry is wrapped in one `ClaudeSDKEntry` event carrying `raw_entry: dict[str, Any]` verbatim, plus its `type` / `uuid` / `timestamp` fields promoted for projection convenience.

Cross-framework readers need canonical events, not opaque CLI entries. The JSONL shape is fully decomposable — the content-block vocabulary (`text` / `thinking` / `tool_use` / `tool_result`) and field names are exactly the Anthropic Messages API, which is structurally identical to MAF .NET's shape. The canonical mapping onto `SCHEMA_v2.md §3` lives in the read-side decomposer [`kurrent_claude_agent_sdk.decompose`](./kurrent_claude_agent_sdk/decompose.py).

**The decomposer is read-side**, not write-side: it operates on already-persisted `ClaudeSDKEntry` streams and produces `(canonical_event, metadata)` tuples a caller can append to a parallel canonical stream. This preserves the adapter's `load(append) == entries` invariant unconditionally; write-side decomposition would require the canonical encoding itself to be lossless, an extra constraint with no benefit. See §10 Q3.

The decomposer lives inside this package (cheap reuse of the schema; no extra install for users who want both writes and canonical reads). If a deployed subscriber runtime emerges later and the read-only consumer footprint starts to matter, extract into `kurrent-claude-agent-decomposer` — the mapping itself has no dependency on `claude-agent-sdk`.

### 4.1 Mapping (schema v2)

| CLI entry → block | Canonical event(s) | Notes |
|---|---|---|
| `user.message.content` (string) | 1× `UserMessageReceived` | `content`=string; `message_id`=entry `uuid`. |
| `user.message.content[]` `tool_result` | N× `ToolResultReceived` | One per block; `is_error` → `extensions.claude_sdk.is_error`; list-of-text content flattened, non-textual content JSON-encoded. Missing `tool_use_id` → deterministic fallback `{entry_uuid}:tr{index}` with `extensions.claude_sdk.missing_tool_id=true`. |
| `user.message.content[]` `text` (multi-block prompt) | 1× `UserMessageReceived` | Text blocks concatenated. |
| `assistant.message.content[]` `text` | 1× `AssistantTextGenerated` per block | Preserves ordering within a turn. |
| `assistant.message.content[]` `thinking` | 1× `AssistantThinkingGenerated` per block | Claude Code thinking is plaintext → `encrypted=False`. `content` from the block's `thinking` field; `signature` from the block's `signature` if present. Unknown block keys ride under `extensions.claude_sdk.thinking_extras`. SCHEMA_v2 §3.2 / §6.1. |
| `assistant.message.content[]` `tool_use` | 1× `AssistantToolCallsGenerated` | All tool_use blocks grouped into one event, emitted **at the position of the first `tool_use` block** so text / thinking / tool relative order is preserved. Missing `id` → deterministic fallback `{entry_uuid}:tc{index}` (empty `call_id` would collide on the schema join key). Non-dict `input` preserved under `{"_raw": value}` rather than coerced to `{}`. |
| `assistant.message.usage` on a content-less turn | 1× `AssistantTextGenerated(content=None)` carrier | Rare post-v2 (thinking is now its own event) — safety net for unexpected content shapes so `$usage` still rides on an assistant event per SCHEMA_v2 §3.6. |
| `assistant.message.usage` | `$usage` KurrentDB event metadata | Canonical slots: `input_tokens` / `output_tokens` / `total_tokens` (preferred from provider, fallback to computed) / `cached_input_tokens` (from `cache_read_input_tokens`) / `reasoning_tokens` / optional `model`. **Everything else is catch-all** — any key the provider emits that isn't a canonical slot rides verbatim under `additional_counts` (formalised in SCHEMA_v2 §3.6). A future Anthropic field is preserved automatically. |
| `assistant.message.stop_reason` / `.id` | `extensions.claude_sdk.stop_reason` / `.anthropic_message_id` on first emitted event | — |
| `attachment` / `system` / `permission-mode` / `last-prompt` / `file-history-snapshot` / `queue-operation` | nothing | CLI-internal; preserved verbatim on `ClaudeSDKEntry` only. |

**Known partiality.** CLI built-in tools (`Read`/`Write`/`Bash`/…) are resolved inside the CLI and never surface as `tool_use`/`tool_result` blocks — only MCP-backed tools do. Canonical decomposition of a CLI-tool-heavy session will show assistant text but not the tool turns. See §8.

## 5. Stream layout

Schema v2 naming, via helpers from `kurrent_agent_schema.streams`:

- **Main transcript:** `AgentSession-{session_id}` — via `agent_session_stream(session_id)`. Carries one `SessionStarted` marker (first touch) plus one `ClaudeSDKEntry` per JSONL line.
- **Subagent transcript:** `AgentSubsession-{session_id}-{agent_id}` — via `agent_subsession_stream(session_id, agent_id)`. `agent_id` is the final path component of the SDK's `SessionKey.subpath`: `subagents/agent-abc123` → `agent-abc123`. Per SCHEMA_v2 §3.5, subagent streams **do not** carry their own `SessionStarted` — that role is fulfilled by `SubagentStarted` on the parent stream, which this adapter does not emit today (the `SessionStore` protocol doesn't expose subagent lifecycle signals; see §10 Q1).

This replaces the v1 convention `AgentSession-{id}__{normalised_subpath}`. The migration is breaking on the stream-name axis; no production data exists today, so no dual-read path.

`project_key` is stashed on `SessionStarted.extensions.claude_sdk` and on every `ClaudeSDKEntry.extensions.claude_sdk.project_key` for traceability; it doesn't scope the stream name. The SDK's contract guarantees `session_id` uniqueness per project so this is safe.

### 5.1 Extension slug

This integration writes under `extensions.claude_sdk` — the slug reserved for the verbatim-entry integration (SCHEMA_v2 §5.2). Note: `extensions.claude_code` is a separate, **Capacitor-owned** slug for CLI-level coding-agent fields (cwd, git state, repo/PR metadata, permission tool_input, worktree_path). This adapter does not write under `claude_code`.

## 6. app_name / user_id

The Claude Agent SDK has no native app/user concept. `KurrentDBSessionStore` takes `app_name` and `user_id` as optional constructor kwargs. They populate `SessionStarted.app_name` / `.user_id` on first write of the main transcript, matching the other integrations' convention (see SCHEMA_v2 §3.1). Omit them for single-tenant deployments.

## 7. At-most-once delivery

The SDK contract: "Exceptions are logged; the subprocess continues unaffected. At-most-once delivery — failed batches are not retried." Our `append` honours this: we wrap the append in `try/except Exception`, log, and drop the batch. Local disk remains the source of truth for durability; a missed KurrentDB batch is a mirroring gap, not a correctness bug at the CLI level.

If you need stronger guarantees (e.g. replacing local disk with KurrentDB as the primary store), that's a different product — and would need a different SDK integration point than `SessionStore`.

## 8. Known limitation: tool-call granularity

Tool calls invoked by the CLI's built-in tools (`Read`, `Write`, `Bash`, `Grep`, etc.) aren't visible to the Python SDK — the CLI resolves them internally and surfaces only the final assistant message. The transcript entries we mirror reflect this. Fine-grained tool-call audit requires MCP-backed tools instead, whose calls do surface at the SDK layer.

This is a property of the SDK, not our integration — documented here so users aren't surprised.

## 9. Out of scope for v0

- Canonical decomposition subscriber (mapping recorded in §4.1; runtime implementation TBD — see §10 Q3).
- `SubagentStarted` / `SubagentCompleted` emission on the parent stream. The `SessionStore` protocol gives us `append(key)` with a new `subpath` on first subagent touch, but no lifecycle signals for start/complete — see §10 Q1.
- `list_sessions` / `delete` / `list_subkeys` / `list_session_summaries` (all deliberately absent from the adapter — SDK probes for presence at runtime and skips when missing).
- Memory and artifact classes parallel to `KurrentDBAgentMemory` / `KurrentDBAgentArtifacts`. The SDK doesn't ship either concept itself.
- Hook-mutation capture — if a `PreToolUse` hook modifies tool input, the delta isn't visible to the SessionStore. Would need SDK-side instrumentation.
- Streaming-event capture for UI replay (SDK's `StreamEvent` messages). Not persisted by the CLI transcript; would require a separate subscriber.

## 10. Open questions

1. **Subagent discovery and lifecycle.** The v2 stream layout (`AgentSubsession-{session}-{agent_id}`) admits subagent enumeration by `$ce-AgentSubsession` scan, but emitting `SubagentStarted` / `SubagentCompleted` on the parent stream requires lifecycle signals the `SessionStore` protocol doesn't currently expose. Revisit when a concrete resume-with-subagents test case is available or when the SDK adds hooks.
2. **Per-project metadata stream.** `list_sessions` could be backed by a `$ce-AgentSession` category scan filtered by `project_key`. Needs the KurrentDB category projection enabled (ours is).
3. **Canonical decomposition trigger.** ~~Write-side in the adapter, or read-side via a separate subscriber?~~ **Decided (DEV-1508): read-side.** Keeps the adapter's `load(append) == entries` invariant unconditional; decomposer lives in its own module against an already-persisted `ClaudeSDKEntry` stream. Implementation TBD.
