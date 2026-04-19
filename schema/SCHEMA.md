# Kurrent Agent Event Schema (v0.1 draft)

A portable event schema for KurrentDB-backed agent persistence, readable and writable by:

- **Microsoft Agent Framework (.NET)** — `Kurrent.AgentFramework` (existing)
- **Microsoft Agent Framework (Python)** — `kurrent-agent-framework` (spike)
- **Google ADK (Python)** — `kurrentdb-adk` (greenfield, this repo)

Cross-framework continuation of a session is an explicit design goal. A session written by an ADK agent must be readable by an AFW agent (and vice versa) with no loss of conversational content. Framework-specific runtime state (workflow checkpoints, resume markers, rewind operations) is preserved in a framework extension envelope but ignored by the other framework.

## 1. Design principles

1. **Canonical-first.** A minimal set of framework-agnostic event types is the primary wire format. Each framework maps its native model onto these types.
2. **Additive extensions.** Every canonical event carries an optional `extensions` block keyed by framework slug (`"adk"`, `"afw"`). A writer places non-portable state in its own block; a reader passes unknown blocks through untouched.
3. **Already aligned.** The .NET and Python AFW packages already share an event schema with snake_case JSON, matching field names, and Pydantic v2 / System.Text.Json round-trip. This document formalises that schema as the canonical form and specifies the ADK mapping onto it.
4. **Forward-compatible by construction.** Readers use `extra="ignore"` (Pydantic) or `JsonUnmappedMemberHandling.Skip` (.NET) so adding a field is non-breaking within a major version.
5. **Identifiers are opaque.** `session_id`, `user_id`, and `tenant_id` are free-form strings. Namespacing (e.g., ADK's `app_name` in `tenant_id`) is a convention, not a schema constraint.

## 2. Stream layout

### 2.1 Shared streams (written and read by all three frameworks)

| Stream | Category | Purpose |
|---|---|---|
| `AgentSession-{session_id}` | `AgentSession` | Conversation events for one session. Primary shared stream. |
| `AgentMemory` (or `AgentMemory-{scope}`) | `AgentMemory` | Retained facts. Scope suffix is optional for multi-tenant deployments. |
| `AgentArtifact-{scope}-{filename}` | `AgentArtifact` | Binary artifact versions. |
| `EvalRun-{run_id}` | `EvalRun` | Eval scores and completion. |

`{session_id}` is opaque. ADK encodes `(app_name, user_id, session_id)` into it when needed; AFW uses the session id directly. The category prefix (`AgentSession-`) is fixed so `$ce-AgentSession` works across frameworks.

### 2.2 Framework-specific streams (ignored by non-owner frameworks)

| Stream | Owner | Purpose |
|---|---|---|
| `AppState-{app_name}` | ADK | App-scoped state (`app:` prefix). |
| `UserState-{app_name}-{user_id}` | ADK | User-scoped state (`user:` prefix). |
| `Credentials-{app_name}-{user_id}` | ADK | Tool OAuth credentials. |
| `WorkflowCheckpoint-{id}` | AFW | Workflow superstep checkpoints. |
| `GroupChat-{id}` | AFW | Multi-agent group-chat turn history. |

A framework seeing a stream it doesn't own should not read or write to it. Cross-framework session continuation does not depend on any stream in this table.

## 3. Canonical events

All events share:

- `timestamp: datetime` (ISO-8601, required) — when the event was written.
- `extensions: dict[str, dict] | None = None` — framework extension envelope. Keys are framework slugs (`"adk"`, `"afw"`); values are opaque to other frameworks.

JSON keys are snake_case. Datetimes serialise with timezone.

### 3.1 Session lifecycle

**`SessionStarted`** — first event of the session stream.

| Field | Type | Req | Notes |
|---|---|---|---|
| `app_name` | string? | no | Application identifier. Required semantically for ADK (drives state/artifact scoping). AFW packages that lack a native notion of "app" supply this via configuration at framework-integration level. |
| `agent_name` | string? | no | Primary agent name (ADK: root agent). |
| `model` | string? | no | Model identifier. |
| `tenant_id` | string? | no | Tenancy key, orthogonal to `app_name`. Reserved for multi-tenant deployments where one app serves multiple tenants. |
| `user_id` | string? | no | End-user identifier. |
| `timestamp` | datetime | yes | |

`app_name`, `tenant_id`, and `user_id` form the scoping triple for app-level state, user-level state, memory, artifacts, and credentials. All three are opaque strings; `app_name` must satisfy ADK's `str.isidentifier()` rule when written by ADK (see `DESIGN.md` §6), but the canonical schema accepts any non-empty string so AFW writers remain unconstrained.

**`SessionEnded`** — marks logical end of the session. Stream is not truncated.

| Field | Type | Req |
|---|---|---|
| `reason` | string? | no |
| `timestamp` | datetime | yes |

### 3.2 Conversation events

All four carry `message_id`, `author_name`, `created_at`, `message_index`, `timestamp`. `message_index` is monotonic per session, assigned by the writer; readers should treat it as advisory (KurrentDB's own append order is authoritative).

**`UserMessageReceived`**

| Field | Type | Req |
|---|---|---|
| `content` | string? | no |
| `message_id` | string? | no |
| `author_name` | string? | no |
| `created_at` | datetime? | no |
| `message_index` | int | yes |
| `timestamp` | datetime | yes |

**`AssistantTextGenerated`** — assistant message with no tool calls. Same fields as `UserMessageReceived`.

**`AssistantToolCallsGenerated`** — assistant message that invokes one or more tools.

| Field | Type | Req |
|---|---|---|
| `tool_calls` | [`ToolCallInfo`] | yes |
| `content` | string? | no (text that accompanies the call, if any) |
| `message_id`, `author_name`, `created_at`, `message_index`, `timestamp` | — | as above |

`ToolCallInfo` = `{ call_id: string, tool_name: string, arguments: object? }`.

**`ToolResultReceived`** — one event per tool response.

| Field | Type | Req |
|---|---|---|
| `call_id` | string | yes (matches `ToolCallInfo.call_id`) |
| `tool_name` | string? | no |
| `result` | string? | no (JSON-encoded if structured) |
| `message_id`, `author_name`, `created_at`, `message_index`, `timestamp` | — | as above |

### 3.3 Multi-agent

No canonical multi-agent events in v1. The two frameworks use different handoff models:

- ADK exposes an LLM-driven `transfer_to_agent` tool that redirects execution within a static agent tree (`src/google/adk/tools/transfer_to_agent_tool.py`, `flows/llm_flows/agent_transfer.py`). This surfaces as an ADK-specific `AgentTransferred` event in the session stream — see §4.
- AFW uses explicit orchestration via `KurrentDBGroupChatManager.selectNext` and records `AgentTurnTaken` in a separate `GroupChat-{id}` stream.

Unifying these under one canonical event would lose semantics either way. Both forms remain framework-specific until a concrete cross-framework use case for "agent handoff" emerges.

### 3.4 Usage

Token usage is preserved as **KurrentDB event metadata** under the key `$usage` on assistant events (`AssistantTextGenerated`, `AssistantToolCallsGenerated`), matching the .NET `UsageCapture` convention. Schema:

```json
{
  "input_tokens": 1507,
  "output_tokens": 203,
  "total_tokens": 1710,
  "cached_input_tokens": 0,
  "reasoning_tokens": 0,
  "model": "gemini-2.5-flash"
}
```

The standalone `TokenUsageRecorded` event type is deprecated for session streams in favour of the `$usage` metadata approach. It remains valid for dedicated usage streams outside the scope of this document.

### 3.5 Evaluation

Written to `EvalRun-{run_id}`. Three events:

- **`EvalRunStarted`** — `{ session_id, scorer, criteria, timestamp }`
- **`TurnScored`** — `{ session_id, turn_index, input?, output?, score, score_label?, reason?, timestamp }`
- **`EvalRunCompleted`** — `{ session_id, turns_scored, average_score, total_cost?, timestamp }`

### 3.6 Memory

**`FactRetained`**

| Field | Type | Req |
|---|---|---|
| `fact` | string | yes |
| `retained_at` | datetime | yes |

**Stream scoping (v1 default: app+user).** Memory must not leak across apps or users by default. The canonical stream name is:

```
AgentMemory-{app_name}-{user_id}
```

Two wider scopes are **reserved** for a future revision but not implemented in v1:

| Stream | Scope | Status |
|---|---|---|
| `AgentMemory-{app_name}-{user_id}` | per-app, per-user | **v1 default** |
| `AgentMemory-{app_name}` | app-wide, shared across users | reserved — v2 |
| `AgentMemory` | global, shared across apps and users | reserved — AFW legacy; supported read-only for v1 compatibility |

Writers in v1 emit only to the per-user scope. Readers consult the per-user stream first; support for wider scopes requires explicit caller opt-in and is out of scope until we have concrete shared-memory use cases. AFW's current single-stream `AgentMemory` is treated as legacy: existing streams remain readable, but new writes from either framework go to the scoped form.

### 3.7 Artifacts

Written to `AgentArtifact-{scope}-{filename}`.

**`ArtifactVersionCreated`**

| Field | Type | Req |
|---|---|---|
| `version` | int | yes (monotonic, starts at 0) |
| `mime_type` | string? | no |
| `inline_bytes` | bytes? | no (base64 in JSON) |
| `canonical_uri` | string? | no (external blob store reference) |
| `custom_metadata` | object | no |
| `created_at` | datetime | yes |

Exactly one of `inline_bytes` / `canonical_uri` is expected.

## 4. Framework-specific event types in the session stream

These live in `AgentSession-{session_id}` alongside canonical events. Non-owning frameworks **must** tolerate them on read (ignore) and **must not** emit them.

| Event type | Owner | Purpose |
|---|---|---|
| `AgentTransferred` | ADK | LLM-driven handoff within an ADK agent tree (triggered by the `transfer_to_agent` tool). Fields: `from_agent?`, `to_agent`, `timestamp`. |
| `Rewind` | ADK | Marks a rewind boundary; fields: `rewind_before_invocation_id`, `state_delta`, `timestamp`. Readers folding state must special-case. |
| `Compaction` | ADK | Inline summary of an event range; fields: `start_timestamp`, `end_timestamp`, `compacted_content` (a canonical content value), `timestamp`. |
| `StateDelta` | ADK | Session-scoped state change (non-app, non-user). Fields: `delta: object`, `invocation_id?`, `timestamp`. |

### 4.1 How AFW reads an ADK-written session

- Canonical conversation events → reconstructed as `Message` objects.
- `AgentTransferred`, `Rewind`, `Compaction`, `StateDelta` → skipped during `Message` reconstruction. The conversation will appear as if the rewind never happened (events prior to the rewind remain visible in the history). For AFW use cases, this is acceptable; AFW has no rewind concept and a different multi-agent model.
- `extensions.adk` on any canonical event → ignored.

### 4.2 How ADK reads an AFW-written session

- Canonical conversation events → reconstructed as ADK `Event` objects (see §5.2).
- No `Rewind`/`Compaction`/`StateDelta` present → no state folding beyond what the canonical events carry; `Session.state` starts empty.
- `extensions.afw` on any canonical event → ignored (AFW currently stores nothing here).

## 5. Framework mappings

### 5.1 AFW `Message` → canonical

Already implemented in `kurrent_agent_framework.chat_history._message_to_events`:

- `Message(role="user", contents=[text])` → `UserMessageReceived`
- `Message(role="assistant", contents=[text])` → `AssistantTextGenerated`
- `Message(role="assistant", contents=[..., function_call, ...])` → `AssistantToolCallsGenerated`
- `Message(role="tool", contents=[function_result])` → one `ToolResultReceived` per result

No `extensions.afw` needed for the current AFW feature set.

### 5.2 ADK `Event` → canonical

One ADK `Event` may decompose into multiple canonical events. The mapping:

| ADK `Event` shape | Canonical event(s) | Notes |
|---|---|---|
| `author == "user"`, text content | `UserMessageReceived` | |
| Non-user author, text content only | `AssistantTextGenerated` | |
| Non-user author, `get_function_calls()` non-empty | `AssistantToolCallsGenerated` + possibly `AssistantTextGenerated` if there's also text | |
| `get_function_responses()` non-empty | One `ToolResultReceived` per response | |
| `actions.transfer_to_agent` set | `AgentTransferred` (ADK-specific; §4) | |
| `actions.state_delta` with `app:` keys | (none — routed to `AppState-{app}` stream) | |
| `actions.state_delta` with `user:` keys | (none — routed to `UserState-{app}-{user}` stream) | |
| `actions.state_delta` with unprefixed keys | `StateDelta` (ADK-specific) | |
| `actions.rewind_before_invocation_id` set | `Rewind` (ADK-specific) | |
| `actions.compaction` set | `Compaction` (ADK-specific) | |

**`extensions.adk` carries everything canonical doesn't:**

```json
{
  "adk": {
    "invocation_id": "inv_123",
    "branch": "root.worker_2",
    "long_running_tool_ids": ["call_abc"],
    "actions": {
      "agent_state": { ... },
      "end_of_agent": true,
      "skip_summarization": false,
      "escalate": null,
      "artifact_delta": {"notes.txt": 3},
      "requested_auth_configs": { ... },
      "requested_tool_confirmations": { ... },
      "render_ui_widgets": null
    },
    "llm_response": {
      "grounding_metadata": { ... },
      "cache_metadata": { ... },
      "citation_metadata": { ... },
      "input_transcription": null,
      "output_transcription": null,
      "avg_logprobs": null,
      "logprobs_result": null,
      "finish_reason": "stop",
      "interrupted": false
    }
  }
}
```

The `adk.actions.agent_state`, `adk.actions.end_of_agent`, and `adk.long_running_tool_ids` fields are **load-bearing for resumability** (see `DESIGN.md` §9). ADK must never drop them on round-trip. A reader that doesn't understand `extensions.adk` is guaranteed not to strip it by Pydantic's `extra="ignore"` semantics only on *input* fields; writers that round-trip must preserve the extensions block by construction. **AFW readers do not round-trip — they consume — so this is safe.**

### 5.3 `app_name` at the framework boundary

`app_name` is first-class on canonical `SessionStarted`. ADK derives it from `App.name` directly. AFW has no native "app" concept; the AFW integrations provide `app_name` as configuration when constructing the history provider — e.g., a constructor kwarg or settings field. If an AFW session is started without an `app_name`, the field is left `null` on the event; downstream consumers that scope by `app_name` (e.g. the `AgentMemory-{app}-{user}` stream) treat that case as "no app scoping" and either require the caller to supply one or fall back to a configured default.

`tenant_id` remains separate and orthogonal. A multi-tenant deployment running one app may populate `tenant_id` per tenant while `app_name` stays constant.

## 6. Versioning

- **Schema version is a single integer.** The current version is `1`. Documented in this file.
- **Within a major version:** additive changes only. New optional fields on existing events; new event types; new extension blocks.
- **Across majors:** breaking changes permitted. Writers should stamp a `$schema_version` metadata field on every event for readers to detect mismatches. Proposal: default to `1`; omit if `1` for wire compactness.
- **Framework packages declare a supported schema range.** Initial: all three support `1`.

## 7. Non-portable features (enumerated)

Explicit list of things a cross-framework reader will not reconstruct:

- **ADK LLM-driven agent transfer.** `AgentTransferred` is ADK-specific (see §4). AFW multi-agent uses a different model.
- **ADK resumability of `LoopAgent` / `ParallelAgent` iterations.** Requires `actions.agent_state` + `actions.end_of_agent` + ordering. ADK-only.
- **ADK rewind.** Not semantically meaningful for AFW.
- **ADK event compaction.** AFW can read the `Compaction` event as an opaque summary, but will not re-trigger compaction logic.
- **AFW workflow checkpoints.** Stored in `WorkflowCheckpoint-{id}`, not in the session stream. ADK ignores.
- **AFW group chat.** Stored in `GroupChat-{id}`, not the session stream. ADK ignores (ADK has its own multi-agent model).
- **ADK app-scoped and user-scoped state.** Stored in separate streams; no AFW equivalent.
- **ADK OAuth credentials.** Stored in `Credentials-...`; no AFW equivalent yet.
- **Cross-user or cross-app memory sharing.** Not supported in v1; memory is strictly `AgentMemory-{app}-{user}` scoped.

## 8. Open questions

Resolved since v0.1 draft:

- ✅ `app_name` is first-class on canonical `SessionStarted` (§3.1). AFW supplies it via configuration.
- ✅ `AgentTransferred` is ADK-specific, living in §4. AFW will model group-chat handoff separately if needed.
- ✅ Memory scoping is `AgentMemory-{app}-{user}` by default; wider scopes reserved for v2 (§3.6).

Remaining:

1. **Artifact stream key convention** — ADK uses `(app, user, session?, filename)`; AFW has no artifacts yet. Stream names proposed as `AgentArtifact-{app}-{user}-{session}-{filename}` / `AgentArtifact-{app}-{user}-{filename}`. Confirm once AFW implements artifacts.
2. **`$schema_version` encoding** — put on every event's metadata, or once in a stream metadata event? Proposal: per-event, default `1`, omit when equal to writer's current.
3. **AFW-Python legacy `AgentMemory` single-stream mode** — how do we migrate existing streams, or do we leave them readable and expect new writes to use scoped form? Proposal: read-compatible forever; writes always scoped.
4. **Shared memory (v2 scope)** — what does caller opt-in to wider scopes look like? Candidate: a `scope` parameter on `search_memory` / `retain_memory` accepting `"user"` (default), `"app"`, `"global"`. Needs a concrete use case before specifying.
5. **Should the schema live in a separate repo?** Candidates: `kurrent-agent-schema` as a small standalone package with Pydantic types + JSON Schema, consumed by ADK and AFW packages. Reduces drift.

## 9. Next steps

1. Review this schema with the AFW (.NET and Python) maintainers. Confirm §5.1, §3.4 (usage-as-metadata), §8 open questions.
2. If accepted: extract the canonical events into a shared package (`kurrent-agent-schema`) so ADK, AFW-.NET, AFW-Python all depend on the same definitions.
3. Update `DESIGN.md` §5 to reference this schema: instead of storing `Event` verbatim as `AdkEvent`, ADK decomposes into canonical events + `extensions.adk`. The "verbatim round-trip" guarantee now relies on the extensions envelope carrying every non-canonical field.
4. Port the AFW Python memory service to require a scope (or keep the single-stream default with a compatibility flag).
5. Add cross-framework round-trip tests: write a session with ADK, read and continue with AFW, write back with AFW, read final state with ADK.
