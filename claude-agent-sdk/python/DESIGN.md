# Kurrent Claude Agent SDK (Python) — Design

KurrentDB `SessionStore` adapter for the [Claude Agent SDK (Python)](https://github.com/anthropics/claude-agent-sdk-python). Shares the canonical event schema with the other integrations in this monorepo (see [`../../schema/SCHEMA.md`](../../schema/SCHEMA.md)).

## 1. Goal

A pip-installable `kurrent-claude-agent-sdk` package exposing `KurrentDBSessionStore`. Wired via `ClaudeAgentOptions(session_store=...)`, it mirrors every transcript entry the Claude Code CLI writes locally to a KurrentDB stream, and reconstructs entries on `--resume`.

## 2. Why this SDK is architecturally different

The other integrations in this monorepo target frameworks that own their own conversation state at the Python layer. The Claude Agent SDK is a **subprocess wrapper**: the Python code spawns the Claude Code CLI, the CLI writes JSONL transcripts to `CLAUDE_CONFIG_DIR`, and the Python SDK receives parsed `Message` objects as the CLI emits them. The SDK is stateless.

The integration point is the `SessionStore` protocol (`src/claude_agent_sdk/types.py:1169`) — an adapter that receives a **secondary copy** of every JSONL line after the local disk write succeeds. This is deliberately post-hoc: local durability is the source of truth, the adapter is for mirroring.

Consequence: `KurrentDBSessionStore` is a thin streaming mirror, not a full session service. It does not own conversation state at runtime.

## 3. Contract

The SDK's `SessionStore` protocol has two required methods and four optional:

| Method | Required? | Our implementation |
|---|---|---|
| `append(key, entries)` | ✅ | Wrap each entry as a `ClaudeSDKEntry` event; append to `AgentSession-{session_id}` for the main transcript or `AgentSession-{session_id}__{normalised_subpath}` for subagents (see §5). Exceptions are logged — subprocess keeps running per SDK contract. |
| `load(key)` | ✅ | Read every `ClaudeSDKEntry` from the stream for `key` (same naming rule as `append`); return `entry.raw_entry` dicts in stream order. Returns `None` when no entries exist for `key` — the stream is missing, has no `ClaudeSDKEntry` events at all, or no entries match the requested `subpath` scope. |
| `list_sessions(project_key)` | Optional | **Absent** — the SDK probes via `hasattr` and skips when missing. Follow-up can wire this to a `$ce-AgentSession` projection. |
| `list_session_summaries(project_key)` | Optional | **Absent.** Added in SDK 0.1.65 for incrementally-maintained summaries computed inside `append()`. Adding it means giving up at-most-once/fire-and-forget semantics, so deferred. |
| `delete(key)` | Optional | **Absent** — no-op per SDK contract for append-only stores. A future tombstone-marker event could fulfil it without breaking the append-only invariant. |
| `list_subkeys(key)` | Optional | **Absent** — main transcript only. Follow-up to discover subagent transcripts. |

> The optional methods must be **absent from the class**, not defined-but-raising. The SDK duck-types presence with `hasattr`; a defined-but-raising method is still "present" and surfaces the error instead of falling back. See commit `6a33c21`.

## 4. Entries are stored verbatim; canonical view comes from a read-side projection

The SDK documents that `SessionStoreEntry` is a discriminated union whose concrete shape is internal and unstable, and that the only guaranteed invariant is **`load(append(entries)) == entries`** (deep-equal, not byte-equal). We respect that: every entry is wrapped in one `ClaudeSDKEntry` event carrying `raw_entry: dict[str, Any]` verbatim, plus its `type` / `uuid` / `timestamp` fields promoted for projection convenience.

Cross-framework readers need canonical events, not opaque CLI entries. Inspecting a live transcript (DEV-1508) confirmed the JSONL shape is fully decomposable — the content-block vocabulary (`text` / `thinking` / `tool_use` / `tool_result`) and field names are exactly the Anthropic Messages API, which is structurally identical to MAF .NET's shape. The mapping onto `SCHEMA.md §3` events (and the `$usage` metadata shim) is recorded on DEV-1508.

**The decomposer is implemented read-side**, not write-side: a separate subscriber reads `ClaudeSDKEntry` streams and emits canonical events to a parallel stream. This preserves the adapter's `load(append) == entries` invariant unconditionally; write-side decomposition would require the canonical encoding itself to be lossless, which is an extra constraint with no benefit. See §10 Q3.

Known partiality: CLI built-in tools (`Read`/`Write`/`Bash`/…) are resolved inside the CLI and never surface as `tool_use`/`tool_result` blocks — only MCP-backed tools do. Canonical decomposition of a CLI-tool-heavy session will show assistant text but not the tool turns. This is §8, not the decomposer's fault.

## 5. Stream layout

- **Main transcript:** `AgentSession-{session_id}`. Same canonical prefix as every other integration.
- **Subagent transcripts:** `AgentSession-{session_id}__{normalised_subpath}`. The SDK addresses these via `SessionKey.subpath = "subagents/agent-{id}"`; we flatten the `/` to `_` and percent-encode anything outside the safe set before appending to the base stream name.

`project_key` is stashed on `SessionStarted` and on every `ClaudeSDKEntry`'s `extensions.claude_sdk.project_key` for traceability; it doesn't scope the stream name itself. The SDK's contract guarantees `session_id` uniqueness per project so this is safe.

## 6. app_name / user_id

The Claude Agent SDK has no native app/user concept. `KurrentDBSessionStore` takes `app_name` and `user_id` as optional constructor kwargs. They populate `SessionStarted.app_name` / `.user_id` on first write per session, matching the other integrations' convention (see `SCHEMA.md §5.3`). Omit them for single-tenant deployments.

## 7. At-most-once delivery

The SDK contract: "Exceptions are logged; the subprocess continues unaffected. At-most-once delivery — failed batches are not retried." Our `append` honours this: we wrap the append in `try/except Exception`, log, and drop the batch. Local disk remains the source of truth for durability; a missed KurrentDB batch is a mirroring gap, not a correctness bug at the CLI level.

If you need stronger guarantees (e.g. you're replacing local disk with KurrentDB as the primary store), that's a different product — and would need a different SDK integration point than `SessionStore`.

## 8. Known limitation: tool-call granularity

Tool calls invoked by the CLI's built-in tools (`Read`, `Write`, `Bash`, `Grep`, etc.) aren't visible to the Python SDK — the CLI resolves them internally and surfaces only the final assistant message. The transcript entries we mirror reflect this. Fine-grained tool-call audit requires MCP-backed tools instead, whose calls do surface at the SDK layer.

This is a property of the SDK, not our integration — documented here so users aren't surprised.

## 9. Out of scope for v0

- Canonical decomposition subscriber (tracked under DEV-1508 step 2; mapping recorded, implementation TBD — see §4).
- `list_sessions` / `delete` / `list_subkeys` / `list_session_summaries` (all deliberately absent from the adapter — SDK probes for presence at runtime and skips when missing).
- Memory and artifact classes parallel to `KurrentDBAgentMemory` / `KurrentDBAgentArtifacts`. Tracked as follow-ups; the SDK doesn't ship either concept itself.
- Hook-mutation capture — if a `PreToolUse` hook modifies tool input, the delta isn't visible to the SessionStore. Would need SDK-side instrumentation.
- Streaming-event capture for UI replay (SDK's `StreamEvent` messages). Not persisted by the CLI transcript; would require a separate subscriber.

## 10. Open questions

1. **Subagent discovery.** `list_subkeys` is how the SDK materialises subagent transcripts on resume. We can fulfil this with a stream-name regex scan over `$all`, or by indexing subpaths into a per-session helper stream. Revisit when a concrete resume-with-subagents test case is available.
2. **Per-project metadata stream.** `list_sessions` could be backed by a `$ce-AgentSession` category scan filtered by `project_key`. Needs the KurrentDB category projection enabled (ours is).
3. **Canonical decomposition trigger.** ~~Write-side in the adapter, or read-side via a separate subscriber?~~ **Decided (DEV-1508): read-side.** Keeps the adapter's `load(append) == entries` invariant unconditional; decomposer lives in its own module against an already-persisted `ClaudeSDKEntry` stream. Implementation TBD (step 2 of DEV-1508).
