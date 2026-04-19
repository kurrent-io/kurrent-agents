# Kurrent ADK (Python) — Design

KurrentDB integration for [Google ADK Python](https://github.com/google/adk-python), modelled on the existing [Kurrent AgentFramework](https://github.com/kurrent-io/agent-framework-dotnet) integration for Microsoft Agent Framework.

One write path to KurrentDB; everything else — chat history, memory, workflow resumability, group-chat audit, evaluation — derives from the same event log.

> **Revision note.** This document evolved across three passes.
> - **v2 (after independent review):** Verbatim `AdkEvent` default, `KurrentDBCredentialService` added, state split across three stream families, explicit concurrency contract, dedicated resume/rewind sections.
> - **v3 (after canonical schema design):** Storage is now **canonical events + `extensions.adk`**, not opaque verbatim. Stream names align with the canonical scheme (`AgentSession-...`, `AgentMemory-...`, etc.) shared with the MS Agent Framework integrations. See `SCHEMA.md` for the canonical event vocabulary and round-trip rules.

## 1. Goal

Provide a pip-installable package (`kurrent-google-adk`) that lets any ADK application persist agent interactions, memory, artifacts, credentials, and evaluation results in KurrentDB by plugging in a small set of service implementations. No changes to user agent code beyond service wiring.

## 2. ADK Plug-points (as of v1.x)

All cited line numbers are against `/Users/alexey/github/adk-python` at the time of writing; treat them as approximate.

| Plug-point | File | Purpose |
|---|---|---|
| `BaseSessionService` | `sessions/base_session_service.py:54` | Session + event storage. Default `append_event` (`:114`) trims temp state and mutates the in-memory `Session`; subclasses override to persist. |
| `BaseMemoryService` | `memory/base_memory_service.py:44` | Long-term memory. `add_session_to_memory`, `add_events_to_memory`, `add_memory`, `search_memory`. |
| `BaseArtifactService` | `artifacts/base_artifact_service.py:88` | Binary artifacts with versioning. |
| `BaseCredentialService` | `auth/credential_service/base_credential_service.py:28` | Tool-call OAuth credential storage — `load_credential`, `save_credential`. |
| `BasePlugin` | `plugins/base_plugin.py:41` | App-wide callbacks: `before/after_run_callback`, `on_event_callback`, `before/after_model_callback`, `before/after_tool_callback`, error callbacks. |
| `EvalSetsManager` | `evaluation/eval_sets_manager.py` | Persist evaluation datasets. |
| `EvalSetResultsManager` | `evaluation/eval_set_results_manager.py:25` | Persist evaluation results. |
| `BaseEventsSummarizer` | `apps/base_events_summarizer.py` | Compaction summarizer. Optional; out-of-scope for v1. |
| `ResumabilityConfig` | `apps/app.py:42` | When `is_resumable=True`, Runner resumes paused invocations from events stored in `SessionService` — no separate checkpoint store abstraction. |
| `App` | `apps/app.py:111` | Top-level container. |
| `Runner` | `runners.py:151` | Constructor accepts `session_service`, `memory_service`, `artifact_service`, `credential_service`. All four can be Kurrent-backed. |

Key implication: **ADK does not need a separate checkpoint store, group-chat manager, or workflow-state abstraction.** Resumability and multi-agent turn history ride on `SessionService`. A single `KurrentDBSessionService` covers three features that required three separate classes in MS AFW — but subject to the semantic constraints in §9 and §10.

## 3. Capability Mapping

| Kurrent.AgentFramework (.NET) | Kurrent ADK (Python) |
|---|---|
| `KurrentDBChatHistoryProvider` | `KurrentDBSessionService` |
| `KurrentDBCheckpointManagerFactory` | *(subsumed by `KurrentDBSessionService` + `ResumabilityConfig`, see §9)* |
| `KurrentDBGroupChatManager` | *(subsumed by `KurrentDBSessionService`)* |
| `UsageCapture` (IChatClient middleware) | `UsageCapturePlugin(BasePlugin)` via `after_model_callback` |
| `IAgentMemory` + `AgentMemoryContextProvider` | `KurrentDBMemoryService` |
| *(new — not in .NET)* | `KurrentDBCredentialService` — tool OAuth tokens |
| `FactExtractionService` (background sub) | Out-of-process KurrentDB catch-up subscriber |
| `EvalRunner` + `TurnScored` events | `KurrentDBEvalSetResultsManager` + `KurrentDBEvalSetsManager` |
| `StreamCoordinator` (cross-process) | Out of scope; ADK has `a2a/` for agent-to-agent |
| Kontext memory adapter | `kurrent-google-adk[kontext]` extra — optional |

## 4. Module layout

```
kurrent_google_adk/
  __init__.py
  client.py                     # KurrentDBClient factory, connection string helpers
  _schema/                      # Canonical event models — initially vendored inline
    __init__.py                 # (will be replaced by an import from
    events.py                   #  `kurrent-agent-schema` once that package exists;
    stream_names.py             #  see SCHEMA.md)
  _revisions.py                 # Last-seen-revision tracking (§8)
  _codec.py                     # Event ↔ canonical decomposition/reconstruction (§5)
  session_service.py            # KurrentDBSessionService
  memory_service.py             # KurrentDBMemoryService
  artifact_service.py           # KurrentDBArtifactService
  credential_service.py         # KurrentDBCredentialService
  plugins/
    __init__.py
    usage_capture.py            # UsageCapturePlugin
  evaluation/
    __init__.py
    set_results_manager.py      # KurrentDBEvalSetResultsManager
    sets_manager.py             # KurrentDBEvalSetsManager
  projections/                  # Opt-in derived views
    __init__.py
    usage.py                    # Token-usage rollups from $usage metadata
  subscriptions/
    __init__.py
    fact_extractor.py           # Background subscriber for cross-session fact extraction
  extras/
    kontext_memory.py           # Optional Kontext-backed memory
```

The `_schema/` subpackage is initially vendored — the same Pydantic models the AFW-Python integration defines. Once a shared `kurrent-agent-schema` package exists (see repo-structure discussion below), `_schema` becomes a thin re-export of that package.

## 5. Event storage — canonical events + `extensions.adk`

The primary write format is the canonical event vocabulary defined in [`SCHEMA.md`](./SCHEMA.md). That same schema is used by the .NET and Python Microsoft Agent Framework integrations, so a session written by ADK is directly readable by AFW agents and vice versa (see `SCHEMA.md §4.1` for the cross-framework read semantics).

ADK's `Event` is richer than any single canonical event:

- Inherits ~20 fields from `LlmResponse` (`models/llm_response.py`): `grounding_metadata`, `usage_metadata`, `cache_metadata`, `custom_metadata`, `citation_metadata`, `input_transcription`, `output_transcription`, `avg_logprobs`, `logprobs_result`, `interaction_id`, `live_session_resumption_update`, `go_away`, `finish_reason`, `interrupted`, `content`, `partial`, etc.
- Adds `invocation_id`, `author`, `actions: EventActions`, `long_running_tool_ids`, `branch`, `id`, `timestamp` (`events/event.py:47-75`).
- `EventActions` carries 11 fields (`events/event_actions.py:51-114`): `skip_summarization`, `state_delta`, `artifact_delta`, `transfer_to_agent`, `escalate`, `requested_auth_configs`, `requested_tool_confirmations`, `compaction`, `end_of_agent`, `agent_state`, `rewind_before_invocation_id`, `render_ui_widgets`.

### 5.1 Decomposition on write

`KurrentDBSessionService._event_to_canonical` decomposes each ADK `Event` into one or more canonical events following `SCHEMA.md §5.2`. Summary:

| ADK event shape | Canonical event(s) emitted |
|---|---|
| `author == "user"`, text content | `UserMessageReceived` |
| Non-user author, text content only | `AssistantTextGenerated` |
| Non-user author, tool calls (± text) | `AssistantToolCallsGenerated` (+ `AssistantTextGenerated` if text also present) |
| Tool responses | One `ToolResultReceived` per response |
| `actions.transfer_to_agent` | `AgentTransferred` (ADK-specific, `SCHEMA.md §4`) |
| `actions.state_delta` with `app:` keys | Routed to `AgentAppState-{app_name}` |
| `actions.state_delta` with `user:` keys | Routed to `AgentUserState-{app_name}-{user_id}` |
| `actions.state_delta` with unprefixed keys | `StateDelta` (ADK-specific, same session stream) |
| `actions.rewind_before_invocation_id` | `Rewind` (ADK-specific) |
| `actions.compaction` | `Compaction` (ADK-specific) |

Token usage (`Event.usage_metadata`) attaches as `$usage` **KurrentDB event metadata** on the emitted assistant event, matching the .NET `UsageCapture` convention (`SCHEMA.md §3.4`).

### 5.2 `extensions.adk` — lossless carrier for non-canonical fields

Everything that doesn't land on a canonical field rides under `extensions.adk` on each emitted event. Shape spelled out in `SCHEMA.md §5.2`; load-bearing fields that must never be dropped on round-trip:

- `extensions.adk.invocation_id`
- `extensions.adk.branch`
- `extensions.adk.long_running_tool_ids` (pause semantics — §9)
- `extensions.adk.actions.agent_state` (resume — §9)
- `extensions.adk.actions.end_of_agent` (resume — §9)

All remaining `EventActions` fields and every `LlmResponse` field go under `extensions.adk.actions.*` and `extensions.adk.llm_response.*` respectively.

### 5.3 Reconstruction on read

`KurrentDBSessionService._canonical_to_event` reverses the decomposition. Canonical events that share an `extensions.adk.invocation_id` and author are merged back into one ADK `Event` when they originated from a single decomposition (e.g. assistant text + tool calls). All fields in `extensions.adk` are restored verbatim. Round-trip structural equality is a hard test invariant (§12).

### 5.4 Why not opaque verbatim?

v2 of this design stored each `Event` as an opaque `AdkEvent` payload. That's been dropped: the canonical form gives cross-framework interop at the **same** fidelity (lossless via `extensions.adk`). An opaque `AdkEvent` would not be readable by AFW agents; the canonical form is strictly more capable.

## 6. Stream naming

Stream naming follows the canonical scheme in `SCHEMA.md §2`. Shared prefixes are important — `$ce-AgentSession` gives cross-framework session discovery across ADK and AFW.

```
AgentSession-{session_id}                        session events (shared with AFW)
AgentAppState-{app_name}                         app-scoped state — ADK-specific
AgentUserState-{app_name}-{user_id}              user-scoped state — ADK-specific
AgentArtifact-{app_name}-{user_id}-{session_id}-{filename}
AgentArtifact-{app_name}-{user_id}-{filename}    user-scoped (no session_id)
AgentCredentials-{app_name}-{user_id}            tool OAuth — ADK-specific
AgentMemory-{app_name}-{user_id}                 memory (SCHEMA.md §3.6 default scope)
EvalRun-{run_id}                                 eval scores
$ce-AgentSession                                  system category stream (shared)
```

Note that the canonical `AgentSession-{session_id}` doesn't encode `app_name`/`user_id` — ADK puts those on the `SessionStarted` event (`app_name` + `tenant_id` + `user_id`; `SCHEMA.md §3.1`) and reads them back from there for scoping downstream streams. ADK callers choose `session_id` freely; no encoding is required beyond the normalisation rules below.

### 6.1 Id validation and normalisation

- **`app_name`** must satisfy `str.isidentifier()` and not equal `"user"` (ADK's own rule, `apps/app.py:30`). Use as-is in stream names.
- **`user_id`, `session_id`, `filename`, `run_id`** are free-form in ADK. Percent-encode any character outside `[a-zA-Z0-9_.-]` before interpolation; reject ids longer than a 128-char segment budget. Normalisation is reversible.

### 6.2 State scope routing

`EventActions.state_delta` can contain keys across all three scopes. On `append_event`, the service partitions the delta by prefix:

| Prefix | Target stream |
|---|---|
| `app:` | `AgentAppState-{app_name}` |
| `user:` | `AgentUserState-{app_name}-{user_id}` |
| `temp:` | Never persisted (base class already strips; see `base_session_service.py:140`). |
| (none) | Session stream, as an ADK-specific `StateDelta` event (`SCHEMA.md §4`) |

Non-state parts of the event — content, remaining `EventActions` fields, `LlmResponse` fields — always ride in the session stream on canonical events and their `extensions.adk` blocks. App/user state never duplicates into per-session streams.

On read, `get_session` reads the three streams independently and folds them into `Session.state` in a defined order: app → user → session, most-recent-write-wins. App/user streams outlive any single session, so reads must be cached — see §8.

## 7. Component designs

### 7.1 `KurrentDBSessionService`

```python
class KurrentDBSessionService(BaseSessionService):
    def __init__(
        self,
        client: KurrentDBClient,
        *,
        state_cache: StateCache | None = None,
    ): ...
```

Behaviour:

- **`create_session`.** Generates `session_id` if absent; appends canonical `SessionStarted` (with `app_name`, `user_id`, agent name, model, etc.) to `AgentSession-{session_id}` with `expected_revision=NO_STREAM`. Returns an empty `Session` with any provided `state` applied (not persisted yet — callers typically follow up with `append_event` carrying a `state_delta`).
- **`get_session`.** Reads `AgentSession-{session_id}` end-to-end. For each canonical event (or ADK-specific event in the same stream), reconstructs ADK `Event` objects via `_canonical_to_event` (§5.3). Reads relevant revisions of `AgentAppState-{app}` and `AgentUserState-{app}-{user}` via `state_cache`. Folds state deltas from all three sources with rewind handling (§10). Applies `GetSessionConfig` filters (`num_recent_events`, `after_timestamp`) to the event list only; state is always fully reconstructed.
- **`append_event`.** Delegates to `super().append_event(session, event)` first so the base class handles `Event.partial` short-circuit (`base_session_service.py:116`), temp-state application, and temp-delta trimming. Then decomposes the `Event` into canonical + `extensions.adk` form (§5.1) and appends. See §8 for the concurrency contract and §10 for rewind/compaction handling.
- **`list_sessions`.** Reads `$ce-AgentSession`, filters by `app_name`/`user_id` (both are on the `SessionStarted` event, not in the stream name), returns session metadata without events or state (matches `ListSessionsResponse` contract, `base_session_service.py:45`).
- **`delete_session`.** Soft delete: appends `SessionEnded`. Stream remains for audit. A `hard_delete=True` kwarg can tombstone via `TombstoneStream`; deferred until a user asks for it.

### 7.2 `KurrentDBMemoryService`

Implements `BaseMemoryService` against `AgentMemory-{app_name}-{user_id}` (`SCHEMA.md §3.6`). Each retained memory entry becomes a canonical `FactRetained` event. Default `search_memory` returns all entries — parity with the .NET `KurrentDBAgentMemory` baseline. `kurrent-google-adk[kontext]` replaces the search with hybrid BM25 + vector retrieval over the same event stream.

Wider scopes (`AgentMemory-{app}` app-shared, `AgentMemory` global) are reserved by the schema but not implemented in v1 — `DESIGN.md §13` open question.

### 7.3 `KurrentDBArtifactService`

One stream per artifact: `AgentArtifact-{app}-{user}-{session}-{filename}` (or `AgentArtifact-{app}-{user}-{filename}` when `session_id=None`). Each `save_artifact` call emits a canonical `ArtifactVersionCreated` event (`SCHEMA.md §3.7`); version equals stream revision (first save returns `0`, per `BaseArtifactService` contract in `artifacts/base_artifact_service.py:88`).

Binary payloads above a configurable threshold (default `1 MiB`) are offloaded; the event carries `canonical_uri` pointing to external storage (S3, GCS, filesystem — pluggable via a small `BlobSink` interface). Smaller payloads stay inline as base64.

### 7.4 `KurrentDBCredentialService`

Implements `BaseCredentialService.load_credential` / `save_credential`. Keys off `auth_config.get_credential_key()` (stable string ADK generates from the auth scheme + scopes). Credentials are written to `AgentCredentials-{app_name}-{user_id}` as `CredentialSaved` events keyed by the credential key; read is a backward scan for the most-recent matching key.

Credential events are ADK-specific — no AFW counterpart today, so they sit outside the canonical schema. Encryption-at-rest is out of scope for v1; a pluggable `CredentialCipher` interface can be added later without a breaking change.

### 7.5 `UsageCapturePlugin`

```python
class UsageCapturePlugin(BasePlugin):
    def __init__(self): super().__init__(name="kurrent_usage_capture")

    async def after_model_callback(self, *, callback_context, llm_response):
        # llm_response.usage_metadata is already on the response.
        # KurrentDBSessionService writes it as KurrentDB event metadata under
        # "$usage" when the resulting Event is persisted (SCHEMA.md §3.4).
        # This plugin exists for observability hooks (logging, metrics).
        # Return None to avoid replacing the response.
```

The plugin is optional — `$usage` metadata is emitted by `KurrentDBSessionService` regardless, pulled straight from `Event.usage_metadata`. The plugin adds room for application-level observability (logs, Prometheus, cost estimation) and exists for parity with the .NET `UsageCapture` hook users will recognise.

### 7.6 Evaluation managers

`KurrentDBEvalSetsManager` stores eval case definitions; `KurrentDBEvalSetResultsManager` writes eval results to `EvalRun-{run_id}` as canonical `EvalRunStarted`, `TurnScored`, `EvalRunCompleted` events (`SCHEMA.md §3.5`). Direct port of the .NET `EvalRunner` event model — same schema.

### 7.7 Background fact extractor

Out-of-process script using a KurrentDB catch-up subscription on `$ce-AgentSession`. For each new `UserMessageReceived` / `AssistantTextGenerated` event, invoke a user-supplied `FactExtractor` callable and append `FactRetained` events to the corresponding `AgentMemory-{app_name}-{user_id}`. Ships as `python -m kurrent_google_adk.subscriptions.fact_extractor`.

## 8. Concurrency contract

ADK's current `DatabaseSessionService` combines three mechanisms: an in-process `asyncio.Lock` per session key, row-level `SELECT ... FOR UPDATE`, and an in-memory `_storage_update_marker` on `Session` to detect stale writers (see `sessions/session.py:53`).

KurrentDB provides optimistic concurrency via `expected_revision`; we must cover the stale-writer case ourselves.

### Rules

1. **Per-session revision tracking.** `KurrentDBSessionService` keeps a `_revisions` map: `(app, user, session_id) → last_known_revision`. Updated after every successful append. Held in memory — not persisted.
2. **Append protocol.** For each decomposed KurrentDB append (session stream, app-state stream, user-state stream) the service passes `expected_revision = last_known_revision`.
3. **Retry policy.** On `WrongExpectedVersion`:
   - Read the stream from `last_known_revision` forward to catch up.
   - Fold any new state deltas into the in-memory `Session.state` (callers already hold the `Session` instance; we mutate it in place, matching base-class semantics).
   - Retry the append against the new revision once.
   - If it fails again, raise `StaleSessionError` (mirroring the ADK stale-session contract). The caller is a second writer and must decide whether to re-read `get_session` and retry.
4. **Cross-stream atomicity.** An `Event` with state deltas spans up to three streams (session + app-state + user-state). KurrentDB does not provide multi-stream transactions. We append in a deterministic order — session → app-state → user-state — and document that a crash between appends leaves only the already-written streams updated. Readers folding state cannot distinguish this from a legitimate partial write; the next successful append will re-include any missing delta because `Session.state` in memory has the full picture. This is acceptable given ADK's own Runner already serialises appends within one invocation (see `runners.py:921-945`).
5. **State stream caching.** App- and user-state streams grow slowly and are read on every `get_session`. The optional `StateCache` caches `(revision, folded_state_dict)` per stream; invalidation happens on append. In-memory cache for v1; pluggable later.

### What this looks like for users

Single-process workloads (the common case): zero conflicts, zero overhead beyond a map lookup.

Multi-process workloads (web UI editing a session while a Runner is live; `Runner.rewind_async` invoked from a different process): one retry-after-catch-up per conflict. Users who expect heavy contention should serialise at the application layer, same as they would with ADK's stock SQL session service.

## 9. Resume semantics

`ResumabilityConfig(is_resumable=True)` in `App` activates ADK's resume flow. Resume depends on three pieces of event state that must round-trip exactly:

- **`actions.agent_state`** (`event_actions.py:106`). Consumed by `Runner._setup_context_for_resumed_invocation` (`runners.py:~1357`) and by `LoopAgent._run_async_impl` (`agents/loop_agent.py:76-147`) to rebuild `current_sub_agent`, `times_looped`, etc.
- **`actions.end_of_agent`** (`event_actions.py:101`). `ParallelAgent._run_async_impl` (`agents/parallel_agent.py:181`) uses it to skip completed sub-agents.
- **`long_running_tool_ids`** (`events/event.py:55`). Consumed by `InvocationContext.should_pause_invocation` (`agents/invocation_context.py:~367`).

All three fields ride in `extensions.adk` on each canonical event (`extensions.adk.actions.agent_state`, `extensions.adk.actions.end_of_agent`, `extensions.adk.long_running_tool_ids`) and round-trip exactly via the reconstruction path in §5.3. Hard test invariant — see §12.

**Ordering matters.** `get_session` must return events in append order. KurrentDB's read order is append order per stream, so this is automatic for session-stream events. State deltas from app/user streams are folded into `Session.state` before returning — they never appear as separate `Event` entries in `Session.events`.

**Branch handling.** `Event.branch` (`events/event.py:60`) scopes events to sub-agents. Stored under `extensions.adk.branch` on every emitted canonical event and also denormalised into KurrentDB event metadata for projection filtering. No separate per-branch stream — branch is filterable at read time but doesn't change storage layout.

## 10. Rewind and compaction semantics

Two ADK features insert *meta-events* that change the meaning of earlier events. A naive fold-forward projection gives wrong results without special-casing.

### Rewind

`Runner.rewind_async` (`runners.py:635-684`) appends an event with `actions.rewind_before_invocation_id` set and a `state_delta` that undoes prior deltas.

**Read-time handling.**
- When folding `state_delta`s into `Session.state`, the fold walks events in append order. On hitting a rewind event, the fold discards every prior delta whose originating event has `invocation_id == rewind_before_invocation_id` or later, then applies the rewind event's own `state_delta` (the compensating delta).
- `Session.events` returned to the caller includes the rewind event but excludes the rewound invocations, matching what ADK expects from `get_session` after a rewind.
- Rewind events are emitted under KurrentDB event type `Rewind` (ADK-specific, `SCHEMA.md §4`), carrying `rewind_before_invocation_id`, `state_delta`, and `timestamp`. AFW readers skip them entirely — they have no rewind concept.

### Compaction

`EventsCompactionConfig` (`apps/app.py:63-108`) emits an event with `actions.compaction: EventCompaction` summarising a time range. `apps/compaction.py` relies on being able to find these events and inspect `start_timestamp`, `end_timestamp`, `compacted_content` on them.

Compaction events are emitted as KurrentDB event type `Compaction` (ADK-specific, `SCHEMA.md §4`), carrying the three fields above as a canonical payload. `get_session` returns them inline in `Session.events` exactly as appended. We do not decompose, summarise, or project them — ADK's own compaction machinery handles everything else. AFW readers skip them (no AFW counterpart).

## 11. Wiring (user-facing API sketch)

```python
from google.adk import Agent
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from kurrent_google_adk import (
    KurrentDBSessionService,
    KurrentDBMemoryService,
    KurrentDBArtifactService,
    KurrentDBCredentialService,
    client as kdb_client,
)
from kurrent_google_adk.plugins import UsageCapturePlugin

kdb = kdb_client.from_connection_string("kurrentdb://localhost:2113?tls=false")

root_agent = Agent(name="my_agent", model="gemini-2.5-flash", instruction="...", tools=[...])

app = App(
    name="my_app",
    root_agent=root_agent,
    plugins=[UsageCapturePlugin()],  # optional
    resumability_config=ResumabilityConfig(is_resumable=True),
)

runner = Runner(
    app=app,
    session_service=KurrentDBSessionService(kdb),
    memory_service=KurrentDBMemoryService(kdb),
    artifact_service=KurrentDBArtifactService(kdb),
    credential_service=KurrentDBCredentialService(kdb),
)
```

## 12. Testing strategy

- **Session-service contract tests.** Port the relevant cases from `tests/unittests/sessions/` (DatabaseSessionService fixtures don't transfer verbatim — expect to adapt). Cover: `create_session` idempotency by `session_id`; `get_session` filter semantics; `append_event` temp-state trim behaviour; `list_sessions` filtering.
- **Concurrency tests.** Spawn two `KurrentDBSessionService` instances pointing at the same KurrentDB, concurrent `append_event` calls, assert the second retries once and the session ends up with both events. Rewind from a second writer while the first is appending.
- **Resume tests.** Using a `LoopAgent` + `ParallelAgent` combo, run N steps, kill, restart, assert resume lands on the correct sub-agent at the correct iteration. Covers `agent_state`, `end_of_agent`, `long_running_tool_ids` round-trip.
- **Rewind tests.** Append a rewind event, assert `get_session` returns the correct `Session.state` and the correct event list.
- **Compaction tests.** Run with `EventsCompactionConfig` enabled, force a compaction, assert the `Compaction` event round-trips and `get_session` returns it inline.
- **Artifact versioning.** Concurrent `save_artifact` calls; assert monotonically increasing revisions.
- **Integration harness.** Testcontainers-Python for KurrentDB; `docker compose` as a fallback.

## 13. Open questions

1. **Artifact inline threshold default.** Starting point: `1 MiB`. Configurable per service instance.
2. **Credential encryption.** Pluggable `CredentialCipher` interface — add in v1 as a no-op default, or defer to v2?
3. **State cache eviction.** App/user state streams can grow large. LRU is an obvious default; need real numbers from a workload to tune.
4. **`run_live` audio artifacts.** Transcription buffering in Runner writes audio-reference events (`runners.py:872-931`). Large `file_data` should probably go through `BlobSink` automatically — confirm the trigger condition.
5. **`$ce-AgentSession` vs. a purpose-built projection.** The system category is free but coarse. A user-defined projection keyed by `(app_name, user_id)` (both on `SessionStarted`) would make `list_sessions` cheaper. Ship with `$ce-...`; add a projection in v2 if needed.
6. **Cross-framework round-trip tests.** Write with ADK, read with AFW-Python, write back from AFW, read final state with ADK. Belongs in a cross-repo test harness — see the repo-structure discussion in the accompanying doc.

## 14. Non-goals

- A2A (agent-to-agent) protocol transport.
- Web UI / `adk web` integration — existing FastAPI endpoints work through the service interfaces unchanged.
- Publishing to PyPI / CI setup / release process.
- Performance benchmarking.
- A Kurrent-backed `BaseEventsSummarizer`.

## 15. Next steps

1. Scaffold the package structure in this repo (`kurrent_google_adk/`) with `pyproject.toml`.
2. Land `KurrentDBSessionService` with verbatim storage + state routing + concurrency contract. Covers the biggest surface.
3. Port ADK's session-service tests and add the concurrency/resume/rewind cases.
4. Add `KurrentDBCredentialService` second (needed before any tool-using sample).
5. Add `KurrentDBArtifactService`, `KurrentDBMemoryService`, eval managers in that order.
6. Optional: typed-events projection and fact-extractor subscriber as v1.1 scope.
