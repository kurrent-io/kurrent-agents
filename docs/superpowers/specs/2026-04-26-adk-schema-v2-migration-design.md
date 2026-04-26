# Google ADK — schema v2 migration design

**Linear:** [DEV-1528](https://linear.app/kurrent/issue/DEV-1528) (parent), with sub-issues [DEV-1533](https://linear.app/kurrent/issue/DEV-1533) (code), [DEV-1534](https://linear.app/kurrent/issue/DEV-1534) (fixture tests), [DEV-1535](https://linear.app/kurrent/issue/DEV-1535) (docs).

**Status:** draft, pending user review before writing the implementation plan.

**Date:** 2026-04-26.

## Goal

Replace the hand-rolled event models under `google-adk/python/kurrent_google_adk/_schema/` with the shared `kurrent-agent-schema` (Python) package. Align stream-name builders and `$usage` metadata handling with `schema/SCHEMA_v2.md`. Keep ADK behaviour bit-equivalent for the events ADK already emits, with one additive change: split assistant `Part.thought=True` content into the new canonical `AssistantThinkingGenerated` event.

## Non-goals

- Promoting `Compaction` / `Rewind` / `StateDelta` / `AgentTransferred` from ADK-local to canonical. They remain ADK-specific event types registered alongside the shared canonical set.
- Adding `additional_counts` to ADK's `$usage` payload. ADK's upstream usage metadata maps cleanly onto the canonical slots; an empty `additional_counts` is fine.
- Rolling forward Capacitor or any other consumer of ADK streams.

## Architecture

### Package layout after migration

```
google-adk/python/kurrent_google_adk/
├── __init__.py
├── _codec.py                 # imports kurrent_agent_schema.events + local events.py
├── _revisions.py             # unchanged
├── _serialization.py         # thin adapter over shared registries + 4 ADK-only types
├── _streams.py               # mix of shared builders + ADK-only stream helpers
├── events.py                 # NEW: ADK-specific event types (AgentTransferred, …)
│                             #      and convenience re-exports of shared canonical types
├── artifact_service.py       # imports re-pointed
├── client.py
├── credential_service.py
├── memory_service.py         # imports re-pointed
├── session_service.py        # imports re-pointed
└── …
```

`_schema/events.py`, `_schema/stream_names.py`, and `_schema/__init__.py` are deleted. The `_schema/` directory is removed.

### Event registries

`_serialization.py` builds two private dicts at import time:

```python
_TYPE_TO_NAME = {**EVENT_TYPE_NAMES, **_ADK_LOCAL_TYPE_TO_NAME}
_NAME_TO_TYPE = {**EVENT_TYPE_BY_NAME, **_ADK_LOCAL_NAME_TO_TYPE}
```

`_ADK_LOCAL_*` covers the four ADK-specific types in `events.py`:

- `AgentTransferred`
- `Rewind`
- `Compaction`
- `StateDelta`

**Event-type wire names are unchanged** for the four ADK-only types (`AgentTransferred`, `Rewind`, `Compaction`, `StateDelta`) — they remain ADK-owned, and a stream written before this PR with v1-shaped event-type names still deserialises correctly. Only the *stream prefixes* on the three ADK-owned framework-specific streams change (see §Stream-name layout).

### `$schema_version` stamping

Per SCHEMA_v2 §9, every serialised event carries `$schema_version = 2` on its KurrentDB metadata. Stamped **last** in `serialize()` so a caller-supplied `metadata={}` cannot forge a different wire version. Source of truth: `kurrent_agent_schema.SCHEMA_VERSION`.

### Hardened `deserialize`

Wrap `json.loads` + `model_validate` in a `try/except (json.JSONDecodeError, ValidationError, UnicodeDecodeError)`. On failure, log a warning with `(type, stream_name, stream_position)` and return `None`. Single bad event in a long stream cannot crash `get_session`. Mirrors the Claude SDK PR #25 hardening (commit `a49e04c` in MAF Python originally).

### Stream-name layout

Per SCHEMA_v2 §2.2, ADK-owned framework-specific streams drop the `Agent-` prefix. The library is unshipped — no production data to preserve — so the migration brings code in line with the doc.

| Stream | Source after migration |
|---|---|
| `AgentSession-{session_id}` | `kurrent_agent_schema.streams.agent_session_stream` |
| `AgentMemory-{app_name}-{user_id}` | local `for_memory` (validates `app_name`, normalises `user_id`) wrapping shared builder |
| `AgentArtifact-{app}-{user}-[{session}-]{file}` | local `for_artifact` (validates + normalises) — shared builder is too permissive |
| `EvalRun-{run_id}` | local `for_eval_run` wrapping shared builder + normalisation |
| `AppState-{app_name}` | local-only (ADK-owned) — **renamed from v1 `AgentAppState-`** |
| `UserState-{app_name}-{user_id}` | local-only — **renamed from v1 `AgentUserState-`** |
| `Credentials-{app_name}-{user_id}` | local-only — **renamed from v1 `AgentCredentials-`** |
| `$ce-AgentSession` etc. | local module-level constants, unchanged |

Why locally-wrap shared builders for shared streams: ADK requires `_validate_app_name` (Python identifier regex, rejects `"user"`) and `_normalise_id` (URL-encoding + 128-char segment cap). The shared builders skip both — appropriate for Claude SDK's free-form `session_id`, not for ADK's `app_name`/`user_id`. The local wrappers preserve v1 ID-validation semantics while emitting the v2 stream prefix.

Function names in `_streams.py` stay (`for_session`, `for_app_state`, `for_user_state`, `for_credentials`, `for_memory`, `for_artifact`, `for_eval_run`) — only the wire-format prefix on the three ADK-owned streams changes. Internal call sites need no edits.

### TokenUsage source

`_codec.extract_usage_metadata` constructs a plain `dict[str, Any]` matching `kurrent_agent_schema.usage.TokenUsage`'s field set (`input_tokens`, `output_tokens`, `total_tokens`, `cached_input_tokens`, `reasoning_tokens`). The codec doesn't need to instantiate `TokenUsage` — it stores the dict under the `$usage` metadata key. The migration replaces the local `TokenUsage` import in any consumer of the type, but the algorithm in `session_service.get_session` for re-hydrating `Event.usage_metadata` from the metadata dict via `google.genai.types.GenerateContentResponseUsageMetadata` is unchanged from commit `de2c3eb` (DEV-1479).

### `AssistantThinkingGenerated` emission

ADK `types.Part` carries a `thought: bool` flag. In v1, the codec's `_classify_parts` ignores the flag — thought-text parts collapse into the assistant's `AssistantTextGenerated.content`. v2 promotes thinking to canonical (SCHEMA_v2 §3.2) and gives us the right event type.

**Behaviour change.** A `Part` with `thought=True` and non-empty `text` is emitted as a separate `AssistantThinkingGenerated` event, not merged into `AssistantTextGenerated`. The thinking event:

- carries the part's text in `content`
- sets `encrypted=False` (ADK exposes plaintext thinking)
- leaves `signature=None` (ADK has no signing concept)
- shares `message_id`, `author_name`, `timestamp`, and `extensions.adk.id` with the assistant event from the same source ADK `Event`, so `_reconstruct_one`'s grouping by `extensions.adk.id` re-merges them on read.

`_reconstruct_one` gains an `AssistantThinkingGenerated` branch that builds a `types.Part(text=event.content, thought=True)` so round-trip is lossless.

If both `thought=True` and `thought=False` text parts exist on the same source `Event`, they emit two separate canonical events from one ADK `Event`. The grouping logic already supports multiple canonical events per source `Event` (via `extensions.adk.id`).

### Empty-args preservation (gotcha `ff1540d`)

`_codec._tool_call_info` retains:

```python
arguments=dict(fc.args) if fc.args is not None else None
```

`_reconstruct_one`'s `AssistantToolCallsGenerated` branch retains the matching `args=dict(tc.arguments) if tc.arguments is not None else None`. Pydantic v2's `exclude_none=True` on serialisation drops `arguments=None` entirely (which is correct — args missing means args missing); empty `{}` survives because it's not `None`.

A regression test in `test_codec.py` constructs an ADK event with `FunctionCall(args={})`, round-trips through serialise → deserialise → reconstruct, and asserts `args == {}` on the rebuilt part.

### Forward-compatibility on read

`_serialization.deserialize` returns `None` for unknown event types. Session service iteration treats `None` as "skip and continue" — no behaviour change from today. New v2 canonical events ADK doesn't emit (`InterruptIssued`, `InterruptResolved`, `SubagentStarted`, `SubagentCompleted`, `SessionContinuedAs`) will round-trip on read because they're in `EVENT_TYPE_BY_NAME`, but the codec doesn't reconstruct them as ADK `Event`s — they fall through. If ADK ever consumes a stream another framework wrote into, those events surface as `None` from `canonical_to_events` and are dropped, which is consistent with v1 behaviour for non-ADK-emitted types.

## Tests (DEV-1534 covered here, not in a separate PR)

### `tests/test_serialization.py` (rewrite)

- `$schema_version` stamped on every serialised event.
- `$schema_version` override protection: caller-supplied `metadata={"$schema_version": "v1"}` is overwritten.
- Round-trip each canonical event through `serialize` → `deserialize`, asserting type and field equality.
- Round-trip each ADK-specific event (`AgentTransferred`, `Rewind`, `Compaction`, `StateDelta`).
- Fixture round-trip: load every applicable file in `schema/fixtures/events/*.json`, `model_validate` into the canonical type, `model_dump_json(exclude_none=True, by_alias=True)`, assert byte-equivalent JSON. Applicable fixtures: `SessionStarted`, `UserMessageReceived`, `AssistantTextGenerated`, `AssistantThinkingGenerated`, `AssistantToolCallsGenerated`, `ToolResultReceived`, `FactRetained`, `ArtifactVersionCreated`, `EvalRunStarted`, `EvalRunCompleted`, `TurnScored`. **Skip** `InterruptIssued/Resolved`, `Subagent*`, `SessionContinuedAs` — ADK does not emit these.
- `metadata/usage.json` round-trip via `kurrent_agent_schema.usage.TokenUsage`.
- Hardened deserialize: malformed JSON → `None` + warning log. Validation error on schema mismatch → `None` + warning log.
- Files use `read_text(encoding="utf-8")` explicitly (Windows-flake guard).

### `tests/test_codec.py` (extend)

- Existing tests retained, imports updated.
- New: `thought=True` part splits into `AssistantThinkingGenerated`; `thought=False` part stays in `AssistantTextGenerated`.
- New: round-trip an ADK `Event` with one thought part + one text part + one tool call, assert reconstruction yields three parts in the original order.
- Lock-in: empty-args (`{}` round-trip preserves empty dict, not `None`).
- Lock-in: `extract_usage_metadata` produces dict-equivalent of canonical `TokenUsage` with `reasoning_tokens` populated when ADK reports `thoughts_token_count`.

### `tests/test_session_service.py` (touch)

- New regression: `get_session` re-populates `Event.usage_metadata` for events that had `$usage` metadata on append. Construct an ADK event with `usage_metadata`, append via `append_event`, read via `get_session`, assert the reconstructed `Event.usage_metadata.prompt_token_count` etc. are populated.

### `tests/test_stream_names.py` (rewrite)

- Lock in the v2 wire format for every helper: `for_session("abc")` → `"AgentSession-abc"`; `for_app_state("myapp")` → `"AppState-myapp"`; `for_user_state("myapp", "alice")` → `"UserState-myapp-alice"`; `for_credentials("myapp", "alice")` → `"Credentials-myapp-alice"`; `for_memory(...)` → `"AgentMemory-myapp-alice"`; etc.
- Existing v1 prefix assertions (`AgentAppState-`, `AgentUserState-`, `AgentCredentials-`) get rewritten to the v2 names.
- Validation tests (rejecting non-identifier `app_name`, normalising `user_id` with URL-unsafe chars) carry over unchanged — they exercise the wrappers, not the prefixes.

### `tests/test_schema.py` (delete)

The local schema module is gone; its tests targeted local types. Replaced by `test_serialization.py` fixture round-trip and shared package's own test suite.

## Docs (DEV-1535 covered here)

### `google-adk/python/DESIGN.md`

- §2 references `schema/SCHEMA_v2.md` (not v1).
- Replace "Vendored here until a shared `kurrent-agent-schema` package is extracted" — the package now exists; document the actual layout.
- §5 (codec) gains a paragraph on `thought=True` → `AssistantThinkingGenerated` emission.
- §5 (codec) limitation list updated; LlmResponse metadata still deferred.
- Stream-name section updated to v2 prefixes: `AppState-` / `UserState-` / `Credentials-` for the ADK-owned streams. Note that v1 prefixes (`AgentAppState-` etc.) are abandoned — the library is pre-shipping, so no migration concern.
- `extensions.adk` slug ownership called out explicitly (per SCHEMA_v2 §5.3 convention).

### `google-adk/python/README.md`

- Link `SCHEMA_v2.md`.
- Note `extensions.adk` ownership.
- `$usage` shape sourced from `kurrent_agent_schema.usage.TokenUsage`.
- Drop any v1-only references.

### Monorepo `CLAUDE.md`

- ADK row in the per-integration table: storage description unchanged ("verbatim `Event` with state-scope routing"); add note that canonical types come from `kurrent-agent-schema`.

## Delivery

**Single PR** covering all three sub-issues (DEV-1533 + DEV-1534 + DEV-1535). Mirrors how DEV-1531 (Claude SDK Python schema v2 migration) was delivered as a single PR (#25), and avoids the ordering trap where docs land before code (or tests land before the code they exercise).

**Acceptance:**

- All existing tests pass.
- New round-trip tests against `schema/fixtures/events/*.json` + `metadata/usage.json` pass.
- `test_codec.py`'s thought-part split test passes.
- `test_codec.py`'s empty-args regression test passes.
- `test_session_service.py`'s `usage_metadata` re-hydration regression test passes.
- `test_stream_names.py` locks in the v2 prefixes (`AppState-` / `UserState-` / `Credentials-`).
- `pyproject.toml` depends on `kurrent-agent-schema >= 0.1.1`.
- `_schema/` directory removed.
- DESIGN.md and README.md reference `SCHEMA_v2.md`.
- Monorepo `CLAUDE.md` updated.

## Risk register

| Risk | Mitigation |
|---|---|
| Empty-args tool-call regression (`ff1540d`) | Explicit lock-in test in `test_codec.py`. |
| `Event.usage_metadata` re-hydration regression (`de2c3eb`) | Explicit lock-in test in `test_session_service.py`. |
| Wire-format change on ADK-owned streams (`AgentAppState-` → `AppState-`, etc.) | Acceptable: library is unshipped, no production data. Tests assert the v2 prefixes. |
| Thought-part split changes existing reader behaviour | Behaviour was a v1 bug (thought text bled into `AssistantTextGenerated`); v2 fix is forward-compat (unknown event types skip on read). DESIGN.md calls out the change explicitly. |
| Pydantic schema drift between local and shared types | Fixture round-trip catches structural drift. |
| `$schema_version` collision with caller metadata | `serialize()` stamps last; test asserts override protection. |

## Open questions resolved during brainstorming

- **Q: Promote `Compaction`/`Rewind` to canonical?** No — out of scope. ADK keeps them locally.
- **Q: Rename ADK-owned stream prefixes per SCHEMA_v2 §2.2?** Yes — `AgentAppState-` → `AppState-`, `AgentUserState-` → `UserState-`, `AgentCredentials-` → `Credentials-`. Library is unshipped, no production data to preserve, so we align with the doc rather than entrenching the older code.
- **Q: Emit `AssistantThinkingGenerated` for `thought=True` parts?** Yes — included in DEV-1533.
- **Q: Single PR vs three?** Single PR matching DEV-1531 precedent.
- **Q: `kurrent-agent-schema` version pin?** `>= 0.1.1`, with `[tool.uv.sources]` editable path matching Claude SDK's `pyproject.toml`.
