# Kurrent Agent Event Schema v2 (draft)

**Status:** draft, work-in-progress. Supersedes [`SCHEMA.md`](./SCHEMA.md) (v1) once adopted.

**Scope:** v2 unifies the canonical event vocabulary used by the framework integrations in this repo (Google ADK, Microsoft Agent Framework, Strands, OpenAI Agents, Claude Agent SDK) **and** by [Capacitor](../../kapacitor) / [Capacitor Server](../../kapacitor-server), a Claude-Code-oriented session tracer and eval platform. Prior to v2, the two projects had overlapping but incompatible event shapes. This revision picks a single model.

No formal migration plan for existing Capacitor streams is required: Capacitor is not a production system and ships a `kapacitor history` command that re-imports sessions from Claude Code transcripts on disk. v1 integrations in this monorepo are forward-compatible; see §9.

---

## 1. What's new in v2

Canonical event vocabulary gains four promotions from v1's reserved list (`SCHEMA.md §3.8`), two new structural concepts, and one additive field:

1. **`AssistantThinkingGenerated`** — promoted from reserved. Reasoning-token output is now standard across Claude, Gemini, o-series, DeepSeek R1 — no longer a Claude-specific concern. (§3.2)
2. **`InterruptIssued` / `InterruptResolved`** — promoted from reserved, made load-bearing. Unified model for mid-turn human-in-the-loop: Claude Code permission prompts, Strands `Interrupt`, ADK `requested_tool_confirmations`, and generic approval gates. (§3.3)
3. **Session chaining** — new `previous_session_id` field on `SessionStarted`, and a canonical `SessionContinuedAs` event. Resume/fork/continue is universal. (§3.1)
4. **Subagent stream convention** — `AgentSubsession-{parent_session_id}-{agent_id}` replaces the v1 `AgentSession-{id}__{subpath}` name-mangling. New canonical `SubagentStarted` / `SubagentCompleted` events. (§2, §3.5)
5. **`extensions.claude_code`** — new documented extension slug for Capacitor and for the Claude Agent SDK integration. Carries coding-agent-specific fields (cwd, git branch, repo/PR, plan content, permission tool_input, etc.). (§5.3)
6. **Capacitor-owned streams** admitted as framework-specific streams ignored by non-owners, same pattern as ADK's `AppState-` / `UserState-`. (§2.2)
7. **Hosted-agent runtime** as a distinct non-canonical concern. `AgentRun-{agent_id}` streams model daemon-managed CLI processes — orthogonal to the conversational session model, with late-bound `session_id` linking. (§2.3)
8. **`ToolCallInfo.tool_kind`** — new optional field carrying a vendor-neutral classification of what a tool call does, drawn from ACP's `ToolKind` vocabulary. Lets a consumer read one closed set instead of maintaining a per-vendor tool-name table. Absent stays distinguishable from `other`. (§3.4.1)

No canonical field names are renamed. No canonical field semantics change. v1 writers remain forward-compatible: v2 readers see v1 events unchanged. v1 readers see v2 events under `extra="ignore"` semantics; new canonical event types will be skipped (unknown) but will not cause errors.

---

## 2. Stream layout

### 2.1 Shared streams (all integrations)

Unchanged from v1.

| Stream | Category | Purpose |
|---|---|---|
| `AgentSession-{session_id}` | `AgentSession` | Primary conversation stream. |
| `AgentSubsession-{parent_session_id}-{agent_id}` | `AgentSubsession` | **New in v2.** Subagent conversation stream. Replaces the v1 `AgentSession-{id}__{normalised_subpath}` convention. |
| `AgentMemory-{app_name}-{user_id}` | `AgentMemory` | Retained facts, per-app per-user. |
| `AgentArtifact-{scope}-{filename}` | `AgentArtifact` | Binary artifact versions. |
| `EvalRun-{run_id}` | `EvalRun` | Eval scores and completion. |

### 2.2 Framework-specific streams (ignored by non-owners)

v1 entries unchanged. New in v2:

| Stream | Owner | Purpose |
|---|---|---|
| `AppState-{app_name}` | ADK | App-scoped state. |
| `UserState-{app_name}-{user_id}` | ADK | User-scoped state. |
| `Credentials-{app_name}-{user_id}` | ADK | Tool OAuth credentials. Payload is a base64-encoded, cipher-self-describing wire blob (see `google-adk/python/DESIGN.md` §7.4 — recommended cipher is AES-256-GCM with AAD binding). |
| `WorkflowCheckpoint-{id}` | AFW | Workflow superstep checkpoints. |
| `GroupChat-{id}` | AFW | Multi-agent group-chat turn history. |
| `MetaSession-{slug}` | Capacitor | **New in v2.** Human-readable grouping across chained sessions. Aggregation only; no canonical replay semantics. |
| `User-{owner_id}` | Capacitor | **New in v2.** Per-user index of owned sessions. |
| `AgentRun-{agent_id}` | Capacitor | **New in v2.** Hosted-agent runtime stream — see §2.3. Orthogonal to `AgentSession-`; not part of the conversation model. |

Capacitor-specific **events** (visibility, access grants, meta-session membership) live in these streams — not in `AgentSession-`. See §4.

### 2.3 Hosted-agent runtime streams (Capacitor, non-canonical)

Capacitor's `kapacitor` CLI ships a **daemon** that spawns and supervises Claude CLI processes in PTYs, each with its own git worktree. Each such process is a **hosted agent**, identified by `agent_id`. The daemon emits lifecycle events about this process to `AgentRun-{agent_id}` streams — these are **not** conversational events; they are operational telemetry about a long-running process that happens to run an agent.

Key properties:

- **Orthogonal to sessions.** An `AgentRunStarted` event fires before any conversational session exists; `session_id` is `null` on start and populated later (typically via the SessionStart hook once the CLI establishes a session).
- **One agent, one or more runs.** The stream per `agent_id` accumulates run lifecycles; today in Capacitor, an agent is single-use per invocation, but the model does not preclude reuse.
- **Heartbeats are telemetry, not history.** The daemon emits `AgentRunHeartbeat` every ~30 seconds while the process is alive. These are liveness signals, not conversational turns.
- **Non-canonical.** No other framework integration in this repo hosts agent processes in a daemon. v2 **does not canonicalise** hosted-agent runtime — it documents Capacitor's convention as admitted-but-owned, the same way ADK's `AppState-{app}` is admitted in §2.2.
- **Future integrations** that add a similar daemon concern should reuse the `AgentRun-{agent_id}` stream shape and event names for consistency, even though they remain framework-specific.

Events (see §4.2 for field definitions):

- `AgentRunStarted` — process spawn; optional `session_id` (initially null).
- `AgentRunHeartbeat` — periodic liveness signal with optional stats and (eventually) `session_id`.
- `AgentRunStopped` — process exit with reason and optional stats.

Stream retention is a deployment concern, not a schema concern — see §10 Q3.

### 2.4 Identifier conventions for stream names

All variable-substitution components of canonical stream names (`session_id`, `parent_session_id`, `agent_id`, `app_name`, `user_id`, `scope`, `filename`, `run_id`) MUST conform to the rules below. Framework-specific streams (§2.2) SHOULD follow the same rules where applicable. The shared `kurrent_agent_schema` / `Kurrent.Agent.Schema` packages provide builders that enforce these rules; producers SHOULD call those builders rather than concatenating strings.

**Character set.** ASCII `[A-Za-z0-9._-]+`, max 128 bytes per component. Producers MUST reject or URL-encode anything outside that set.

**GUID-shaped values.** When a component value parses as a UUID/GUID, producers MUST emit it in **lowercase, dashless** form (the .NET `"N"` format, e.g. `8d77fd28fda0485f9ae18ee9c7fc3751`). Readers MUST also accept the hyphenated `"D"` form as a legacy-compat fallback (matches Capacitor's `SessionStreamCandidates`). Lowercase only — case-sensitivity differences between writers would split a single conversation across two streams.

**Non-GUID values.** Used verbatim after the character-set check. Case-preserved.

**Compound suffix separators.** Where a stream name has two components joined by `-` (e.g. `AgentSubsession-{parent_session_id}-{agent_id}`, `AgentMemory-{app_name}-{user_id}`), the separator is a single `-`. Neither component may begin or end with `-`. Inner `-` characters within a component are permitted (so `agent_id = "sub-research-x9k2"` is valid). Cross-framework consumers SHOULD identify a subsession via the `SubagentStarted.subsession_stream` event payload field rather than parsing stream names back into components.

---

## 3. Canonical events

All fields from v1 §3 carry over. This section records only **changes and additions**.

### 3.1 `SessionStarted` (extended)

Adds `previous_session_id` and clarifies `tenant_id`.

| Field | Type | Req | Since | Notes |
|---|---|---|---|---|
| `app_name` | string? | no | v1 | |
| `agent_name` | string? | no | v1 | |
| `model` | string? | no | v1 | |
| `tenant_id` | string? | no | v1 | |
| `user_id` | string? | no | v1 | |
| `agent_config` | `AgentConfig`? | no | v1 | |
| `previous_session_id` | string? | no | **v2** | Prior session this session resumes or forks from. Opaque; readers use it to reconstruct conversation history across resume boundaries. |
| `timestamp` | datetime | yes | v1 | |

**New canonical event:** `SessionContinuedAs`

| Field | Type | Req |
|---|---|---|
| `next_session_id` | string | yes |
| `reason` | string? | no |
| `timestamp` | datetime | yes |

Written to the **predecessor** session's stream immediately before that session ends (or is abandoned), pointing forward. Paired with `previous_session_id` on the successor's `SessionStarted`, this forms a bidirectional chain.

### 3.2 `AssistantThinkingGenerated` (new, promoted from reserved)

Assistant-authored reasoning output, distinct from final answer text. Emitted when a model produces reasoning tokens (Claude extended thinking, o-series, Gemini thinking, R1).

| Field | Type | Req | Notes |
|---|---|---|---|
| `content` | string? | no | Plaintext reasoning. Omitted when the content is encrypted or redacted. |
| `encrypted` | bool | no (default false) | True when the provider returned an opaque blob instead of plaintext reasoning. Observed in OpenAI o-series (encrypted reasoning returned to the caller). Claude (including Claude Code) returns plaintext thinking and sets this to `false`. Gemini thinking is plaintext. The opaque value, when present, goes in `extensions.{framework}.thinking.raw`. |
| `signature` | string? | no | Provider-supplied signature for verification. Populated by OpenAI o-series alongside encrypted content; absent for plaintext thinking. |
| `message_id`, `author_name`, `created_at`, `message_index`, `timestamp` | — | as §3.2 | |

Token usage rides on `$usage` metadata as with other assistant events. `$usage.reasoning_tokens` is the field to populate.

### 3.3 `InterruptIssued` / `InterruptResolved` (new, promoted from reserved)

Mid-turn human-in-the-loop pauses. Generalises Claude Code permission prompts, Strands `Interrupt`, ADK `requested_tool_confirmations` / `requested_auth_configs`, and generic approval gates.

**`InterruptIssued`**

| Field | Type | Req | Notes |
|---|---|---|---|
| `request_id` | string | yes | Opaque correlation id; matched by `InterruptResolved.request_id`. See post-hoc rule below. |
| `kind` | string | yes | One of: `permission`, `approval`, `input`, `auth`. Lowercase, extensible via extensions. |
| `tool_name` | string? | no | Populated when the interrupt blocks a specific tool invocation (kind=`permission`/`approval`/`auth`). |
| `prompt` | string? | no | Human-readable prompt or message shown to the user. |
| `message_id` | string? | no | Anchors the interrupt to its carrier message in frameworks that bundle approval requests inside chat messages (e.g. MAF emits `FunctionApprovalRequestContent` as a content block on an assistant message). Null/absent for standalone interrupts (e.g. Claude Code permission prompts, which are emitted by Capacitor's watcher and have no carrier message). |
| `timestamp` | datetime | yes | |

Tool input / the proposed action / auth challenge details go in `extensions.{framework}.interrupt` on the same event. Canonical stays thin; callers who want to replay a permission decision read the extension block.

When the interrupt blocks a specific tool call (`kind=approval` / `permission`), integrations SHOULD additionally place the proposed call under their extension slug using a uniform shape:

```json
{
  "extensions": {
    "<slug>": {
      "interrupt": {
        "proposed_call": { "id": "...", "name": "...", "arguments": {} }
      }
    }
  }
}
```

Soft convention, not a hard requirement. The slug stays per-framework (`afw`, `claude_code`, `strands`, …) so different frameworks may carry different surrounding metadata in their slug; the field names *underneath* `interrupt.proposed_call` are the convention. Lets cross-framework readers (notably Capacitor's approval-prompt UI) render uniformly without per-slug code.

**`InterruptResolved`**

| Field | Type | Req | Notes |
|---|---|---|---|
| `request_id` | string | yes | Matches `InterruptIssued.request_id`. |
| `outcome` | string | yes | One of: `allow`, `allow_once`, `allow_always`, `deny`, `cancel`, `answered`, `timeout`. |
| `response` | string? | no | User-supplied free-form text (kind=`input`) or a rationale. |
| `message_id` | string? | no | Anchors the resolution to its carrier message in frameworks that bundle approval responses inside chat messages (e.g. MAF emits `FunctionApprovalResponseContent` as a content block on a user message). Null/absent for standalone resolutions. |
| `timestamp` | datetime | yes | |

Framework-specific resolution details (e.g. `permission_decision` enum values, updated tool inputs after a `PreToolUse` hook rewrite) live in `extensions.{framework}.interrupt`.

**`request_id` ↔ `tool_call_id` correlation (post-hoc gating only).** Approval gates split into two modes depending on whether the model has already committed to a tool call when the gate fires:

| Framework | Mode | Notes |
|---|---|---|
| MS Agent Framework `FunctionApprovalRequestContent` | post-hoc | `request_id` = `FunctionCall.Id`; the gate decides whether to execute an already-committed call. |
| Claude Code permission prompts (Capacitor) | pre-hoc | Gate fires before any tool call exists; Capacitor mints a synthetic GUID. |
| Strands `Interrupt` (when adopted) | TBD | Classify per-framework as adoption lands. |
| ADK `requested_tool_confirmations` (when adopted) | TBD | Classify per-framework as adoption lands. |

**Post-hoc rule:** when an `InterruptIssued` is post-hoc and its `InterruptResolved` outcome is in the `allow*` family, the `request_id` MUST equal the `call_id` of the matching `ToolCallInfo` entry within the eventual `AssistantToolCallsGenerated.tool_calls` list. MAF satisfies this naturally because both events use the same `FunctionCall.Id`. Cross-event correlation is therefore trivial without per-framework decoding.

**Pre-hoc:** no rule applies. `request_id` is opaque (a synthetic id is fine). Cross-event linkage, when needed, is reconstructed downstream from sequence + `tool_name` or from per-framework extension fields.

### 3.4 Conversation events (one addition)

`UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived` — identical to v1 §3.2, with one addition to `ToolCallInfo`:

**`ToolCallInfo`** = `{ call_id: string, tool_name: string, arguments: object?, tool_kind: string? }`

| Field | Type | Req | Notes |
|---|---|---|---|
| `call_id` | string | yes | Correlates with `ToolResultReceived.call_id`. |
| `tool_name` | string | yes | Raw vendor name, verbatim. Never normalised. |
| `arguments` | object? | no | Free-form JSON. An empty object is preserved as `{}`, not dropped. |
| `tool_kind` | string? | no | Vendor-neutral classification of what the call *does*. New in v2. |

#### 3.4.1 `tool_kind`

`tool_name` is raw vendor fidelity: `Bash`, `shell`, `apply_patch`, `str_replace_editor` all mean "run a command" or "edit a file" but share no vocabulary. `tool_kind` is the vendor-neutral counterpart, so a consumer that wants to know what a call *did* — a trace UI picking an icon, an eval counting file writes, a policy engine gating execution — reads one closed set instead of maintaining its own per-vendor name table.

The vocabulary is [ACP](https://agentclientprotocol.com)'s `ToolKind`, reused verbatim so ACP-native agents pass their own kind straight through:

| Value | Meaning |
|---|---|
| `read` | Reads a file or resource. |
| `edit` | Creates or modifies a file or resource. |
| `delete` | Removes a file or resource. |
| `move` | Moves or renames a file or resource. |
| `search` | Searches for files or content. |
| `execute` | Runs a command or script. |
| `think` | Internal reasoning or planning. |
| `fetch` | Retrieves external content (web, remote API). |
| `switch_mode` | Changes the agent's operating mode. |
| `other` | Classified, and none of the above. |

**Absent and `other` are different answers and MUST stay distinguishable.**

- **absent** — nobody classified this call. The producing integration has no mapping table for this framework yet, or an ACP agent sent no kind. "We don't know."
- **`other`** — classified, and deliberately none of the above: a subagent invocation, a skill, an MCP tool. "We know, and it's none of these."

Collapsing the two is what would stop a consumer trusting the field: a reader counting unclassified calls to decide whether a mapping table is missing cannot tell the cases apart. Producers therefore **omit the field entirely** when they have no classification — never `""`, and never `"other"` as a stand-in for "unknown". Edition-2024 explicit presence plus the `formatDefaultValues: false` / `always_print_fields_with_no_presence=False` writer settings make absence the JSON default; a producer has to go out of its way to emit an empty string.

Unrecognised values are not an error. A reader that receives a token outside the ten above SHOULD treat it as `other` rather than dropping the call — the same normalisation the producing side applies.

`tool_kind` classifies the call, not its outcome. A `read` that failed is still `read`; failure lives on the matching `ToolResultReceived`.

### 3.5 Subagents (new)

Subagent transcripts live in their own streams: `AgentSubsession-{parent_session_id}-{agent_id}`. The parent session stream records subagent lifecycle:

**`SubagentStarted`** (written **atomically to BOTH** the parent `AgentSession-` stream and the `AgentSubsession-` stream via `multi_append`)

| Field | Type | Req | Notes |
|---|---|---|---|
| `agent_id` | string | yes | Opaque, producer-chosen, unique-per-invocation. Recommended shape: `{role_slug}-{short_unique}`. Must satisfy §2.4 character set. |
| `agent_type` | string? | no | Producer-defined role/category string; opaque to canonical readers. Examples: `research`, `code-reviewer`, `general-purpose`, `TriageAgent`. |
| `prompt` | string? | no | |
| `subsession_stream` | string? | no | Full stream name, for reader convenience. |
| `timestamp` | datetime | yes | |

The dual-stream write lets a reader landing on the subsession stream learn its lifecycle without joining back to the parent (Capacitor's trace-tree projector and per-agent eval queries depend on this). The two appends MUST happen in a single `multi_append` call — partial states (parent marker without subsession marker) are not a supported reader state.

**`SubagentCompleted`** (written **atomically to BOTH** the parent `AgentSession-` stream and the `AgentSubsession-` stream via `multi_append`)

| Field | Type | Req | Notes |
|---|---|---|---|
| `agent_id` | string | yes | Same value as on the matching `SubagentStarted`. |
| `outcome` | string? | no | `"success"`, `"error"`, or `"cancelled"`. |
| `summary` | string? | no | Free-text rationale; producers MAY truncate. |
| `timestamp` | datetime | yes | |

Subagent streams carry the full canonical vocabulary (`UserMessageReceived`, `AssistantTextGenerated`, ...). They do **not** carry their own `SessionStarted` / `SessionEnded` — the parent's `SubagentStarted` / `SubagentCompleted` events fulfil that role.

This replaces the v1 `AgentSession-{id}__{normalised_subpath}` convention documented in `claude-agent-sdk/python/DESIGN.md §5`. That file is updated in lock-step with v2 adoption.

### 3.6 Usage (unchanged, clarified)

Token usage is preserved as KurrentDB event metadata under `$usage` on **every** assistant event:

- `AssistantTextGenerated`
- `AssistantToolCallsGenerated`
- `AssistantThinkingGenerated` (new — populate `reasoning_tokens`)

Capacitor's standalone `TokenUsageEvent` is **deprecated** in v2 in favour of `$usage` metadata on the event that consumed the tokens. Same rationale as v1: usage is observability about the event, not conversational payload.

Schema unchanged from v1 §3.4, including the open `additional_counts` bucket for provider-specific counters:

```json
{
  "input_tokens": 1507,
  "output_tokens": 203,
  "total_tokens": 1710,
  "cached_input_tokens": 0,
  "reasoning_tokens": 0,
  "model": "gemini-2.5-flash",
  "additional_counts": { "cache_creation_input_tokens": 40136 }
}
```

See v1 §3.4.1 for the SDK-specific translation table — new integrations should fold known upstream keys into canonical slots and reserve `additional_counts` for genuinely unmapped counters, rather than copying the source dict wholesale.

### 3.7 Memory, artifacts, evaluation (unchanged)

`FactRetained`, `ArtifactVersionCreated`, `EvalRunStarted` / `TurnScored` / `EvalRunCompleted` — identical to v1 §3.5–§3.7. Capacitor does not emit these today but is expected to adopt them as it grows beyond session tracing. New: `SessionScored` (sibling to `TurnScored` for session-level evaluators; see below).

#### `SessionScored`

Emitted once per `(session, evaluation metric)` by a session-level eval runner. Sibling to `TurnScored` for evaluators that score the whole session as a unit (e.g. "did the agent stay on plan?", "were destructive operations justified?") rather than per turn.

Fields:

* `session_id` — the session being evaluated.
* `score` — numeric value with the same `value_missing` convention as `TurnScored`. `0` with `extensions.afw.eval.value_missing = true` when the metric has no numeric meaning (`StringMetric`, or `NumericMetric` with null `Value`).
* `score_label` — metric name. Mirrors `TurnScored.score_label`.
* `reason` — human-readable rationale.
* `timestamp` — emission time.
* `extensions` — framework-specific envelope. `metric_kind`, `is_aggregable`, `interpretation`, `diagnostics`, `string_value` all live under `extensions.afw.eval.*` exactly like `TurnScored`.

A single eval run emits either `TurnScored` events (per-turn evaluator) or `SessionScored` events (session-level evaluator), never mixed.

---

## 4. Framework-specific event types in the session stream

v1 §4 unchanged. New in v2 — Capacitor-owned events, all in Capacitor-owned streams (`MetaSession-`, `User-`, or as visibility markers on `AgentSession-`):

| Event type | Owner | Stream | Purpose |
|---|---|---|---|
| `SessionVisibilitySet` | Capacitor | `AgentSession-` | Access-control change for a session. |
| `SessionVisibilityReset` | Capacitor | `AgentSession-` | Revert visibility to default. |
| `MetaSessionMembershipAdded` | Capacitor | `MetaSession-` | Link a session into a meta-session. |

Non-owners ignore these on read and must not emit them. Canonical readers (ADK/AFW/Strands/OpenAI/Claude SDK) skipping unknown event types is the existing v1 behaviour (`extra="ignore"` / `JsonUnmappedMemberHandling.Skip`).

### 4.2 Hosted-agent runtime events (Capacitor, on `AgentRun-` streams)

All written to `AgentRun-{agent_id}`, **not** to any session stream. Non-owners do not read these streams.

| Event | Fields | Notes |
|---|---|---|
| `AgentRunStarted` | `prompt?`, `model?`, `effort?`, `repo_path?`, `worktree_path?`, `session_id?`, `timestamp` | Process spawn. `session_id` is typically null on start; populated later via heartbeat. |
| `AgentRunHeartbeat` | `session_id?`, `stats?`, `timestamp` | ~30s cadence while the daemon process is alive. `stats` is an opaque record (`AgentRunStats`). |
| `AgentRunStopped` | `reason?`, `stats?`, `timestamp` | Process exit (graceful, crash, user-cancel). |

Coding-agent-specific fields on `AgentRunStarted` (`repo_path`, `worktree_path`, `effort`) are acceptable at the canonical-event level for hosted-agent-runtime because this event type itself is already framework-specific — there is no portable event they could leak into. Capacitor MAY additionally carry richer fields under `extensions.claude_code` on these events.

---

## 5. Extensions

### 5.1 Extension-envelope semantics (unchanged)

Every canonical event carries `extensions: dict[str, dict] | None = None`. Keys are framework slugs. Writers place non-portable fields under their own slug. Readers pass unknown slugs through untouched.

### 5.2 Documented extension slugs

| Slug | Owner | Since |
|---|---|---|
| `adk` | Google ADK integration | v1 |
| `afw` | Microsoft Agent Framework (.NET + Python) | v1 |
| `strands` | Strands integration | v1 |
| `openai` | OpenAI Agents integration | v1 |
| `claude_sdk` | Claude Agent SDK integration (verbatim entries) | v1 |
| `claude_code` | **New in v2.** Capacitor + CLI-level coding-agent fields | v2 |

Additional slugs do not need registration; the list above is documentation of which integrations currently write which blocks.

### 5.3 `extensions.claude_code` field catalogue

Coding-agent-specific fields that used to live directly on Capacitor's event types in v1-era Capacitor. In v2 they are all under `extensions.claude_code`.

On `SessionStarted`:
```json
{
  "claude_code": {
    "cwd": "/Users/alexey/dev/eventstore/kurrent-agents",
    "home_dir": "/Users/alexey",
    "transcript_path": "/Users/alexey/.claude/projects/-Users-alexey.../conversation.jsonl",
    "source": "cli",
    "version": "2.0.1",
    "git_branch": "main",
    "git_user_name": "Alexey Zimarev",
    "git_user_email": "az.admin@kurrent.io",
    "is_sidechain": false,
    "plan_content": "## Goal\n...",
    "owner_github_id": 123456,
    "repo": {
      "owner": "eventstore",
      "name": "kurrent-agents",
      "branch": "main",
      "pr_number": 42,
      "pr_title": "...",
      "pr_url": "https://github.com/eventstore/kurrent-agents/pull/42"
    }
  }
}
```

On `SubagentStarted`:
```json
{
  "claude_code": {
    "repo_path": "/Users/alexey/dev/eventstore/kurrent-agents",
    "worktree_path": "/Users/alexey/dev/eventstore/kurrent-agents-wt-42",
    "effort": "medium"
  }
}
```

On `InterruptIssued` with `kind=permission`:
```json
{
  "claude_code": {
    "permission": {
      "tool_input": { "command": "rm -rf /tmp/foo", "timeout": 5000 },
      "rule_id": "bash:rm",
      "suggestion_type": "once"
    }
  }
}
```

On `InterruptResolved`:
```json
{
  "claude_code": {
    "permission": {
      "behavior": "allow",
      "updated_tool_input": null
    }
  }
}
```

On `ToolResultReceived` for coding-specific tools:
```json
{
  "claude_code": {
    "file_path": "src/foo.py",
    "line_range": [10, 42]
  }
}
```

This is a non-exhaustive catalogue. Adding fields under `extensions.claude_code.*` is non-breaking within v2. Removing or renaming is breaking.

---

## 6. Framework mappings (delta)

v1 §5.1 (AFW) and §5.2 (ADK) carry over unchanged.

### 6.1 Capacitor / Claude Code transcript → canonical (new)

| Claude Code transcript entry | Canonical event(s) | Notes |
|---|---|---|
| `type: "user"`, content text | `UserMessageReceived` | |
| `type: "assistant"`, content blocks: text only | `AssistantTextGenerated` | |
| `type: "assistant"`, content blocks include `tool_use` | `AssistantToolCallsGenerated` (+ `AssistantTextGenerated` if text also present) | Preserve `tool_use_id` as `ToolCallInfo.call_id`. |
| `type: "assistant"`, content blocks include `thinking` | `AssistantThinkingGenerated` (separate event from any accompanying text or tool call) | Claude Code thinking is plaintext — set `encrypted=false`. Populate `content` directly. |
| `type: "user"`, content blocks include `tool_result` | `ToolResultReceived` (one per result) | |
| Permission-prompt entry (from Capacitor watcher) | `InterruptIssued { kind: "permission" }` + `InterruptResolved` | Tool input → `extensions.claude_code.permission`. |
| `type: "system"` with subagent start marker | Parent stream: `SubagentStarted` + open `AgentSubsession-` stream | Subagent `session_id` → `agent_id`. |
| Session resume marker | `SessionStarted { previous_session_id }` on new session; `SessionContinuedAs { next_session_id }` on predecessor | |

Coding-specific fields (cwd, git state, repo, PR metadata, plan content, permission tool_input, worktree_path) go in `extensions.claude_code` on the relevant canonical event — never as first-class fields on canonical events.

### 6.2 Claude Agent SDK integration (updated)

[`claude-agent-sdk/python/DESIGN.md §5`](../claude-agent-sdk/python/DESIGN.md) currently stores opaque `ClaudeSDKEntry` events in `AgentSession-{id}__{normalised_subpath}`. In v2 the integration has two paths:

- **Verbatim path** (default, unchanged from v1): continue to mirror each `SessionStoreEntry` as a `ClaudeSDKEntry` event. The subagent stream naming changes to `AgentSubsession-{parent_session_id}-{agent_id}`.
- **Canonical-decomposition path** (new, opt-in): the adapter also emits canonical events (`UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, `AssistantThinkingGenerated`, `ToolResultReceived`) per the §6.1 mapping. The verbatim `ClaudeSDKEntry` is still emitted to preserve the round-trip invariant `load(append(entries)) == entries`.

Mode is a constructor kwarg on `KurrentDBSessionStore`. Default: `verbatim`. Capacitor's writer defaults to `canonical` (it does not need the round-trip invariant).

---

## 7. Deprecations

| Item | Status in v2 | Replacement |
|---|---|---|
| `TokenUsageRecorded` standalone event in session streams | Deprecated in v1 §3.4; remains deprecated | `$usage` metadata on assistant events |
| Capacitor's `TokenUsageEvent` | **Deprecated in v2** | `$usage` metadata |
| `AgentSession-{session_id}__{subpath}` subagent stream names | **Deprecated in v2** | `AgentSubsession-{parent_session_id}-{agent_id}` |
| Direct coding-agent fields on Capacitor canonical events (cwd, git_branch, repo_*, plan_content, tool_input on permission events) | **Deprecated in v2** | `extensions.claude_code.*` |

Deprecation means: writers should stop emitting the deprecated form; readers must continue to accept it for v2's lifetime.

---

## 8. Non-portable features (updated from v1 §7)

Removed from this list in v2 (now canonical): thinking, permission/approval/input interrupts, subagent lifecycle, session chaining.

Still framework-specific:

- ADK LLM-driven agent transfer (`AgentTransferred`, §4).
- ADK resumability of `LoopAgent` / `ParallelAgent` iterations (`actions.agent_state`, `actions.end_of_agent`).
- ADK rewind.
- ADK event compaction.
- AFW workflow checkpoints (`WorkflowCheckpoint-{id}`).
- AFW group chat (`GroupChat-{id}`).
- ADK app- and user-scoped state.
- ADK OAuth credentials.
- Cross-user / cross-app memory sharing.
- Capacitor visibility and access grants (`SessionVisibilitySet*`, `AccessGrant`).
- Capacitor meta-session aggregation (`MetaSession-`).
- Capacitor heartbeats (`AgentRunHeartbeat`).

---

## 9. Versioning and compatibility

- **Schema version: 2.** Writers stamp `$schema_version = 2` on event metadata. v1 readers see events either without a version (treat as v1) or with `v2` (decide per integration whether to reject or skip unknown events).
- **v2 is additive over v1 on the canonical side.** No canonical field is renamed or removed. v1 writers produce streams that v2 readers consume unchanged.
- **v2 is breaking on the Capacitor side.** Capacitor currently has its own event type names (`UserMessage`, `ToolInvocation`, ...) and stream prefix (`Session-`). v2 requires migration to the canonical names. No backward read support is required: Capacitor has no production data and `kapacitor history` rebuilds streams from transcripts on disk.
- **Claude Agent SDK integration:** the existing `ClaudeSDKEntry` verbatim events remain valid. Only the subagent stream naming changes. The new canonical-decomposition mode is opt-in.

---

## 10. Open questions

1. ~~**Single shared package for canonical types.**~~ ✅ **Resolved 2026-04-21**, **revised 2026-04-27**. Source of truth is now Protobuf at `schema/proto/kurrent/agent/v2/`. The Python (`kurrent-agent-schema`) and .NET (`Kurrent.Agent.Schema`) packages are generated by `buf` (committed `_generated/` and `Generated/` trees) plus a thin hand-written sibling for stream-name builders, JSON helpers, and the type registry. JSON wire format is proto3 canonical with `preserve_proto_field_name=true`; integer-valued numbers inside `google.protobuf.Struct` round-trip via `Value.number_value` (a `double`) and may surface with a `.0` suffix in Python output, which the cross-language CI check normalises. Drift guarded by per-language fixture round-trip tests, a cross-language structural-equivalence job, and a buf-generate diff gate in CI. See `docs/superpowers/specs/2026-04-27-protobuf-schema-design.md`.
2. ~~**`InterruptIssued.kind` vocabulary.**~~ ✅ **Resolved 2026-04-21.** Open string. The documented set (`permission`, `approval`, `input`, `auth`) is guidance for interoperability, not a constraint — enum is too limiting for a field where future integrations may legitimately surface new interrupt kinds. Readers must tolerate unknown values.
3. ~~**AgentRun stream retention.**~~ ✅ **Resolved 2026-04-21.** Capacitor's deployment concern, not a schema concern. The schema records the stream convention (`AgentRun-{agent_id}`); retention policy (unbounded / `MaxAge` / `MaxCount` / stream-per-run) is left to each Capacitor deployment.
4. ~~**Reasoning-content encryption metadata.**~~ ✅ **Resolved 2026-04-21.** Empirically: OpenAI o-series returns encrypted reasoning with a signature; Claude (including Claude Code) returns plaintext thinking; Gemini thinking is plaintext. `encrypted: bool` + optional `signature: string?` covers the observed cases. Opaque blobs (OpenAI only, today) go in `extensions.openai.thinking.raw`. Revisit if a cross-provider verification use case emerges.
5. ~~**Subagent depth.**~~ ✅ **Resolved 2026-04-21.** Flat one-level model is sufficient today. Generic agentic flows treat subagents as tools (tool call returns subagent output), which collapses deeper nesting at the caller's boundary. No observed case of subagent-spawning-subagent in any current integration (Claude Code, Strands, ADK, OpenAI Agents, AFW). Revisit only if a real nested case surfaces; the `AgentSubsession-{parent_session_id}-{agent_id}` convention admits a graph-walk reconstruction later if needed without breaking the flat case.
6. ~~**v1 → v2 transition window.**~~ ✅ **Resolved 2026-04-21.** ASAP. v2 is the target; each integration adopts v2 as soon as DEV-1526 (shared packages) is available and drops v1 read paths in the same cutover. Since no v1-era streams exist in production (Capacitor has `kapacitor history` reimport; other integrations are freshly developed), no long transition window is needed.

---

## 11. Next steps

1. Socialise this draft with AFW maintainers, ADK maintainers, Capacitor maintainers. Expected reviewers: .NET AFW owner (Aaron), Python AFW owner, Capacitor owner.
2. Open Linear issues in each affected project (this repo, Capacitor) capturing the concrete integration work.
3. Decide open question #1 (shared package) before any integration changes land.
4. Update [`SCHEMA.md`](./SCHEMA.md) to point to this document as the successor, and start v2 adoption per-integration in any order (adoptions are isolated because v2 is additive over v1 canonical).
