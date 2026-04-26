# Google ADK schema v2 migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `google-adk/python/kurrent_google_adk/_schema/` with the shared `kurrent-agent-schema` package, align stream names with `SCHEMA_v2.md §2.2`, and emit `AssistantThinkingGenerated` for ADK `Part.thought=True`. Single PR covering DEV-1533 (code), DEV-1534 (fixture tests), DEV-1535 (docs).

**Architecture:** Drop the local Pydantic mirror; re-export shared canonical types and keep four ADK-specific types (`AgentTransferred`, `Rewind`, `Compaction`, `StateDelta`) in a new local `events.py`. Stream-name builders stay locally-named but emit v2 prefixes. `$schema_version=2` stamped last on every serialise; `deserialize` hardened to log + skip on parse errors.

**Tech Stack:** Python 3.11+, Pydantic v2, `kurrent-agent-schema` (≥ 0.1.1, editable path), `kurrentdbclient` (async), pytest + pytest-asyncio, uv, ruff.

**Spec:** `docs/superpowers/specs/2026-04-26-adk-schema-v2-migration-design.md` (commit `a4a4b09`).

**Run tests with:** `cd google-adk/python && uv run pytest tests/ -v`
(or, for a single test, append `tests/test_xxx.py::test_name`).

---

## Task 1: Branch and add `kurrent-agent-schema` dependency

**Files:**
- Modify: `google-adk/python/pyproject.toml`

- [ ] **Step 1: Create the working branch**

```bash
git checkout main
git pull --ff-only
git checkout -b alexeyzimarev/dev-1528-google-adk-migrate-to-canonical-schema-v2
```

- [ ] **Step 2: Add `kurrent-agent-schema` to dependencies**

Open `google-adk/python/pyproject.toml` and edit the `[project] dependencies` table from:

```toml
dependencies = [
  "google-adk >= 1.0.0",
  "kurrentdbclient >= 1.2.0",
  "pydantic >= 2.5",
]
```

to:

```toml
dependencies = [
  "google-adk >= 1.0.0",
  # 0.1.1 is the first release with the schema-v2 canonical event surface
  # (AssistantThinkingGenerated, SubagentStarted/Completed, SessionContinuedAs,
  # InterruptIssued/Resolved) plus TokenUsage.additional_counts.
  "kurrent-agent-schema >= 0.1.1",
  "kurrentdbclient >= 1.2.0",
  "pydantic >= 2.5",
]
```

Then edit `[tool.uv.sources]` from:

```toml
[tool.uv.sources]
kurrent-agents-testing = { path = "../../testing", editable = true }
```

to:

```toml
[tool.uv.sources]
kurrent-agent-schema = { path = "../../schema/python", editable = true }
kurrent-agents-testing = { path = "../../testing", editable = true }
```

- [ ] **Step 3: Sync uv lock**

```bash
cd google-adk/python && uv sync --extra dev
```

Expected: lockfile updates; no errors.

- [ ] **Step 4: Verify imports resolve**

```bash
cd google-adk/python && uv run python -c "from kurrent_agent_schema.events import EVENT_TYPE_NAMES; from kurrent_agent_schema.usage import TokenUsage, USAGE_METADATA_KEY; from kurrent_agent_schema import SCHEMA_VERSION; print(SCHEMA_VERSION)"
```

Expected output: `2`.

- [ ] **Step 5: Run existing test suite to confirm nothing else broke**

```bash
cd google-adk/python && uv run pytest tests/ -x -q
```

Expected: existing tests pass (some integration tests may need Docker — that's fine, they pass when Docker is up).

- [ ] **Step 6: Commit**

```bash
git add google-adk/python/pyproject.toml google-adk/python/uv.lock
git commit -m "chore(adk-python): depend on kurrent-agent-schema (DEV-1528)"
```

---

## Task 2: Create local `events.py` with ADK-specific event types

`AgentTransferred`, `Rewind`, `Compaction`, `StateDelta` stay ADK-owned. Re-export shared canonical types from the same module so internal callers have one import path.

**Files:**
- Create: `google-adk/python/kurrent_google_adk/events.py`

- [ ] **Step 1: Write the new module**

Create `google-adk/python/kurrent_google_adk/events.py`:

```python
"""ADK-specific event types and re-exports of the shared canonical schema.

Four event types live here because they describe ADK-specific session
mechanics (agent handoff, rewind boundary, event-range compaction,
session-scoped state delta) and are not canonical across frameworks. The
remaining canonical types are re-exported from
``kurrent_agent_schema.events`` so internal callers have a single import
path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kurrent_agent_schema.events import (
    _EventBase,
    AgentConfig,
    ArtifactVersionCreated,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    EvalRunCompleted,
    EvalRunStarted,
    FactRetained,
    InterruptIssued,
    InterruptResolved,
    SessionContinuedAs,
    SessionEnded,
    SessionStarted,
    SubagentCompleted,
    SubagentStarted,
    ToolCallInfo,
    ToolResultReceived,
    TurnScored,
    UserMessageReceived,
)
from pydantic import Field

ADK_EXTENSION_KEY: str = "adk"
"""Slug used in ``extensions.<slug>`` for ADK-specific fields. Per
SCHEMA_v2 §5.3 each integration owns one slug; this is ours."""


# --- ADK-specific event types (registered in _serialization alongside canonical) ---


class AgentTransferred(_EventBase):
    """LLM-driven handoff within an ADK agent tree."""

    from_agent: str | None = None
    to_agent: str
    timestamp: datetime


class Rewind(_EventBase):
    """ADK rewind boundary; readers folding state must special-case."""

    rewind_before_invocation_id: str
    state_delta: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class Compaction(_EventBase):
    """Inline summary of an event range produced by ADK event compaction."""

    start_timestamp: datetime
    end_timestamp: datetime
    compacted_content: dict[str, Any]
    timestamp: datetime


class StateDelta(_EventBase):
    """Session-scoped state change (non-app, non-user keys)."""

    delta: dict[str, Any] = Field(default_factory=dict)
    invocation_id: str | None = None
    timestamp: datetime


__all__ = [
    "ADK_EXTENSION_KEY",
    # ADK-specific
    "AgentTransferred",
    "Rewind",
    "Compaction",
    "StateDelta",
    # Re-exported canonical types
    "_EventBase",
    "AgentConfig",
    "ArtifactVersionCreated",
    "AssistantTextGenerated",
    "AssistantThinkingGenerated",
    "AssistantToolCallsGenerated",
    "EvalRunCompleted",
    "EvalRunStarted",
    "FactRetained",
    "InterruptIssued",
    "InterruptResolved",
    "SessionContinuedAs",
    "SessionEnded",
    "SessionStarted",
    "SubagentCompleted",
    "SubagentStarted",
    "ToolCallInfo",
    "ToolResultReceived",
    "TurnScored",
    "UserMessageReceived",
]
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
cd google-adk/python && uv run python -c "from kurrent_google_adk.events import AgentTransferred, Compaction, Rewind, StateDelta, AssistantThinkingGenerated, ADK_EXTENSION_KEY; print('ok', ADK_EXTENSION_KEY)"
```

Expected output: `ok adk`.

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/kurrent_google_adk/events.py
git commit -m "feat(adk-python): add events.py for ADK-specific types + canonical re-exports (DEV-1533)"
```

---

## Task 3: Rewrite `_streams.py` with v2 prefixes (TDD)

Stream-name layout per spec:
- `AgentSession-{session_id}` (canonical, shared)
- `AgentMemory-{app_name}-{user_id}` (canonical, shared)
- `AgentArtifact-{app}-{user}-[{session}-]{file}` (canonical, shared)
- `EvalRun-{run_id}` (canonical, shared)
- `AppState-{app_name}` (ADK-only, **renamed** from v1 `AgentAppState-`)
- `UserState-{app_name}-{user_id}` (ADK-only, **renamed** from v1 `AgentUserState-`)
- `Credentials-{app_name}-{user_id}` (ADK-only, **renamed** from v1 `AgentCredentials-`)

ADK-specific normalisation (`_validate_app_name` regex, `_normalise_id` URL-encoding) stays local.

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/_streams.py`
- Modify: `google-adk/python/tests/test_stream_names.py`

- [ ] **Step 1: Update tests first (failing red phase)**

Open `google-adk/python/tests/test_stream_names.py`. Replace any assertions that reference `AgentAppState-`, `AgentUserState-`, `AgentCredentials-` with the v2 names. The expected values become:

| Helper call | New expected output |
|---|---|
| `for_app_state("myapp")` | `"AppState-myapp"` |
| `for_user_state("myapp", "alice")` | `"UserState-myapp-alice"` |
| `for_credentials("myapp", "alice")` | `"Credentials-myapp-alice"` |
| `for_session("abc-123")` | `"AgentSession-abc-123"` |
| `for_memory("myapp", "alice")` | `"AgentMemory-myapp-alice"` |
| `for_artifact(app_name="myapp", user_id="alice", filename="doc.pdf")` | `"AgentArtifact-myapp-alice-doc.pdf"` |
| `for_artifact(app_name="myapp", user_id="alice", filename="doc.pdf", session_id="s1")` | `"AgentArtifact-myapp-alice-s1-doc.pdf"` |
| `for_eval_run("run-1")` | `"EvalRun-run-1"` |

Validation tests (rejecting `app_name="user"`, rejecting `app_name="123abc"`, URL-encoding `user_id="alice/bob"`) keep their current expected behaviour — only the stream-prefix portion changes. The category constants `CATEGORY_SESSION`, `CATEGORY_MEMORY`, `CATEGORY_ARTIFACT`, `CATEGORY_EVAL_RUN` keep their `$ce-Agent…` values unchanged.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd google-adk/python && uv run pytest tests/test_stream_names.py -v
```

Expected: failures on the renamed-prefix assertions because `_streams.py` still re-exports v1 builders.

- [ ] **Step 3: Rewrite `_streams.py` self-contained**

Replace the entire contents of `google-adk/python/kurrent_google_adk/_streams.py` with:

```python
"""Stream-name builders.

Function names are unchanged from v1 (``for_session``, ``for_app_state``,
``for_user_state``, ``for_credentials``, ``for_memory``, ``for_artifact``,
``for_eval_run``). Output stream prefixes follow ``schema/SCHEMA_v2.md``:

* ADK-owned framework-specific streams drop the ``Agent-`` prefix
  (``AppState-`` / ``UserState-`` / ``Credentials-``) per §2.2.
* Canonical shared-stream names (``AgentSession-``, ``AgentMemory-``,
  ``AgentArtifact-``, ``EvalRun-``) come from the shared package.

ADK-specific id normalisation lives here because the shared builders are
deliberately permissive (Claude SDK uses free-form session ids); ADK requires
``str.isidentifier()``-style ``app_name`` checks and URL-encoding for
``user_id`` / ``filename`` / ``session_id``.
"""

from __future__ import annotations

import re
import urllib.parse

from kurrent_agent_schema.streams import (
    agent_artifact_stream,
    agent_memory_stream,
    agent_session_stream,
    eval_run_stream,
)

# Max length per id segment after normalisation, to keep stream names manageable.
_MAX_SEGMENT_LENGTH = 128

# Characters that pass through unmodified when normalising free-form ids.
_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"

_APP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalise_id(value: str, *, field: str) -> str:
    """Percent-encode any characters outside the safe set and bound the length."""
    if not value:
        raise ValueError(f"{field} cannot be empty")
    encoded = urllib.parse.quote(value, safe=_SAFE_ID_CHARS)
    if len(encoded) > _MAX_SEGMENT_LENGTH:
        raise ValueError(
            f"{field} exceeds {_MAX_SEGMENT_LENGTH}-char segment budget after normalisation "
            f"(got {len(encoded)}): {value!r}"
        )
    return encoded


def _validate_app_name(app_name: str) -> str:
    """ADK's ``App.name`` must satisfy ``str.isidentifier()`` and not equal ``"user"``."""
    if not _APP_NAME_RE.match(app_name):
        raise ValueError(
            f"app_name must be a valid Python identifier (got {app_name!r}); "
            "ADK enforces this in apps/app.py:30."
        )
    if app_name == "user":
        raise ValueError('app_name cannot be "user" (reserved in ADK).')
    return app_name


# ---- Shared canonical streams ----------------------------------------------


def for_session(session_id: str) -> str:
    """Shared session stream. Category prefix: ``AgentSession``."""
    return agent_session_stream(_normalise_id(session_id, field="session_id"))


def for_memory(app_name: str, user_id: str) -> str:
    """Canonical per-app, per-user memory stream (SCHEMA_v2.md §2.1)."""
    return agent_memory_stream(
        _validate_app_name(app_name),
        _normalise_id(user_id, field="user_id"),
    )


def for_artifact(
    *,
    app_name: str,
    user_id: str,
    filename: str,
    session_id: str | None = None,
) -> str:
    """Per-artifact stream. Session-scoped unless ``session_id`` is ``None``."""
    app = _validate_app_name(app_name)
    user = _normalise_id(user_id, field="user_id")
    file = _normalise_id(filename, field="filename")
    if session_id is None:
        return agent_artifact_stream(f"{app}-{user}", file)
    session = _normalise_id(session_id, field="session_id")
    return agent_artifact_stream(f"{app}-{user}-{session}", file)


def for_eval_run(run_id: str) -> str:
    """Evaluation results stream (SCHEMA_v2.md §3.7)."""
    return eval_run_stream(_normalise_id(run_id, field="run_id"))


# ---- ADK-owned framework-specific streams (SCHEMA_v2.md §2.2) ---------------


def for_app_state(app_name: str) -> str:
    """ADK app-scoped state (``app:`` prefix keys)."""
    return f"AppState-{_validate_app_name(app_name)}"


def for_user_state(app_name: str, user_id: str) -> str:
    """ADK user-scoped state (``user:`` prefix keys)."""
    return (
        f"UserState-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
    )


def for_credentials(app_name: str, user_id: str) -> str:
    """ADK-owned tool OAuth credentials stream."""
    return (
        f"Credentials-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
    )


# System category streams — useful for catch-up subscriptions across all sessions.
CATEGORY_SESSION = "$ce-AgentSession"
CATEGORY_MEMORY = "$ce-AgentMemory"
CATEGORY_ARTIFACT = "$ce-AgentArtifact"
CATEGORY_EVAL_RUN = "$ce-EvalRun"


__all__ = [
    "CATEGORY_ARTIFACT",
    "CATEGORY_EVAL_RUN",
    "CATEGORY_MEMORY",
    "CATEGORY_SESSION",
    "for_app_state",
    "for_artifact",
    "for_credentials",
    "for_eval_run",
    "for_memory",
    "for_session",
    "for_user_state",
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd google-adk/python && uv run pytest tests/test_stream_names.py -v
```

Expected: all assertions pass.

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/kurrent_google_adk/_streams.py google-adk/python/tests/test_stream_names.py
git commit -m "refactor(adk-python): rewrite _streams.py with v2 prefixes (DEV-1533)"
```

---

## Task 4: Rewrite `_serialization.py` (TDD for $schema_version + hardened deserialize)

Thin adapter over the shared registries plus the four ADK-only types. `$schema_version=2` stamped last. `deserialize` wraps parse in `try/except` and logs + returns `None` on failure.

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/_serialization.py`
- Modify: `google-adk/python/tests/test_serialization.py`

- [ ] **Step 1: Write failing tests for the new behaviour first**

Replace the entire contents of `google-adk/python/tests/test_serialization.py` with:

```python
"""Serialization round-trip and metadata-stamping tests.

Covers $schema_version stamping, override protection, hardened deserialize,
and round-trip equivalence for canonical + ADK-specific event types.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from kurrent_agent_schema import SCHEMA_VERSION
from kurrent_agent_schema.usage import USAGE_METADATA_KEY, TokenUsage
from kurrentdbclient import RecordedEvent

from kurrent_google_adk import _serialization
from kurrent_google_adk.events import (
    AgentTransferred,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    Compaction,
    Rewind,
    SessionStarted,
    StateDelta,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)


FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "schema" / "fixtures"
EVENTS_FIXTURES = FIXTURES_ROOT / "events"
USAGE_FIXTURE = FIXTURES_ROOT / "metadata" / "usage.json"

# ADK does not emit these; their fixtures aren't expected to round-trip via this adapter.
NON_ADK_FIXTURES = {
    "InterruptIssued.json",
    "InterruptResolved.json",
    "SubagentStarted.json",
    "SubagentCompleted.json",
    "SessionContinuedAs.json",
}


def _ts() -> datetime:
    return datetime(2026, 4, 26, 12, 0, tzinfo=UTC)


def _recorded(new_event) -> RecordedEvent:
    return RecordedEvent(
        type=new_event.type,
        data=new_event.data,
        metadata=new_event.metadata,
        id=new_event.id,
        stream_name="AgentSession-test",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )


# --- $schema_version stamping ------------------------------------------------


def test_serialize_stamps_schema_version_2() -> None:
    event = SessionStarted(timestamp=_ts())
    new_event = _serialization.serialize(event)
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == SCHEMA_VERSION == 2


def test_serialize_caller_metadata_cannot_override_schema_version() -> None:
    event = SessionStarted(timestamp=_ts())
    new_event = _serialization.serialize(event, metadata={"$schema_version": "v1", "$correlation_id": "abc"})
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2
    assert metadata["$correlation_id"] == "abc"


# --- Canonical round-trip ----------------------------------------------------


def test_user_message_round_trip() -> None:
    original = UserMessageReceived(
        content="hello",
        message_id="msg-1",
        author_name="alice",
        message_index=0,
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_assistant_thinking_round_trip() -> None:
    original = AssistantThinkingGenerated(
        content="planning",
        encrypted=False,
        message_id="msg-2",
        author_name="root",
        message_index=1,
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


# --- ADK-specific round-trip -------------------------------------------------


def test_agent_transferred_round_trip() -> None:
    original = AgentTransferred(from_agent="alice", to_agent="bob", timestamp=_ts())
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_rewind_round_trip() -> None:
    original = Rewind(
        rewind_before_invocation_id="inv-7",
        state_delta={"key": "value"},
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_compaction_round_trip() -> None:
    original = Compaction(
        start_timestamp=_ts(),
        end_timestamp=_ts(),
        compacted_content={"summary": "ok"},
        timestamp=_ts(),
    )
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


def test_state_delta_round_trip() -> None:
    original = StateDelta(delta={"k": 1}, invocation_id="inv-3", timestamp=_ts())
    new_event = _serialization.serialize(original)
    decoded = _serialization.deserialize(_recorded(new_event))
    assert decoded == original


# --- Hardened deserialize ----------------------------------------------------


def test_deserialize_unknown_type_returns_none() -> None:
    bad = RecordedEvent(
        type="UnknownEventType",
        data=b"{}",
        metadata=b"",
        id=uuid.uuid4(),
        stream_name="AgentSession-test",
        stream_position=0,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )
    assert _serialization.deserialize(bad) is None


def test_deserialize_malformed_json_logs_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    bad = RecordedEvent(
        type="UserMessageReceived",
        data=b"{not-json",
        metadata=b"",
        id=uuid.uuid4(),
        stream_name="AgentSession-test",
        stream_position=42,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )
    with caplog.at_level("WARNING", logger="kurrent_google_adk._serialization"):
        assert _serialization.deserialize(bad) is None
    assert any("Skipping unparseable event" in rec.message for rec in caplog.records)


def test_deserialize_schema_mismatch_logs_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    # UserMessageReceived requires `message_index: int` and `timestamp: datetime`.
    bad = RecordedEvent(
        type="UserMessageReceived",
        data=b'{"content": "hi"}',
        metadata=b"",
        id=uuid.uuid4(),
        stream_name="AgentSession-test",
        stream_position=43,
        commit_position=0,
        prepare_position=0,
        content_type="application/json",
    )
    with caplog.at_level("WARNING", logger="kurrent_google_adk._serialization"):
        assert _serialization.deserialize(bad) is None
    assert any("Skipping unparseable event" in rec.message for rec in caplog.records)


# --- Fixture round-trip ------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    sorted(p for p in EVENTS_FIXTURES.glob("*.json") if p.name not in NON_ADK_FIXTURES),
    ids=lambda p: p.name,
)
def test_event_fixture_round_trip(fixture: Path) -> None:
    """Each canonical fixture deserialises into the matching canonical type
    and serialises back to byte-equivalent JSON (modulo key ordering)."""
    expected = json.loads(fixture.read_text(encoding="utf-8"))
    event_type_name = fixture.stem
    cls = _serialization._NAME_TO_TYPE[event_type_name]
    event = cls.model_validate(expected)
    actual = json.loads(event.model_dump_json(exclude_none=True, by_alias=True))
    assert actual == expected, f"{fixture.name} drifted on round-trip"


def test_usage_fixture_round_trip() -> None:
    expected = json.loads(USAGE_FIXTURE.read_text(encoding="utf-8"))
    usage = TokenUsage.model_validate(expected)
    actual = json.loads(usage.model_dump_json(exclude_none=True, by_alias=True))
    assert actual == expected


def test_usage_metadata_key_constant() -> None:
    assert USAGE_METADATA_KEY == "$usage"
```

- [ ] **Step 2: Run tests; expect them to fail**

```bash
cd google-adk/python && uv run pytest tests/test_serialization.py -v
```

Expected: many failures (`_serialization` has no `_NAME_TO_TYPE` exposed, `$schema_version` not stamped, hardened deserialize not yet implemented, ADK-specific types not registered).

- [ ] **Step 3: Rewrite `_serialization.py`**

Replace the entire contents of `google-adk/python/kurrent_google_adk/_serialization.py` with:

```python
"""Canonical-event ↔ KurrentDB wire serialization.

Thin adapter over the shared :mod:`kurrent_agent_schema` type registry. The
four event types specific to this integration (``AgentTransferred``,
``Rewind``, ``Compaction``, ``StateDelta``) live in :mod:`.events` and are
registered locally alongside the canonical set.

``$schema_version`` is stamped on every serialised event's metadata per
``schema/SCHEMA_v2.md §9``. It is stamped last so a caller-supplied value in
``metadata={}`` cannot forge a different wire version.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from kurrent_agent_schema import SCHEMA_VERSION
from kurrent_agent_schema.events import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES, _EventBase
from kurrentdbclient import NewEvent, RecordedEvent
from pydantic import ValidationError

from .events import AgentTransferred, Compaction, Rewind, StateDelta

logger = logging.getLogger("kurrent_google_adk._serialization")

SCHEMA_VERSION_METADATA_KEY: str = "$schema_version"
"""Metadata key stamped on every event. See SCHEMA_v2 §9."""

_ADK_LOCAL_TYPES: dict[type[_EventBase], str] = {
    AgentTransferred: "AgentTransferred",
    Rewind: "Rewind",
    Compaction: "Compaction",
    StateDelta: "StateDelta",
}

_TYPE_TO_NAME: dict[type[_EventBase], str] = {
    **EVENT_TYPE_NAMES,
    **_ADK_LOCAL_TYPES,
}
_NAME_TO_TYPE: dict[str, type[_EventBase]] = {
    **EVENT_TYPE_BY_NAME,
    **{name: cls for cls, name in _ADK_LOCAL_TYPES.items()},
}


def name_for(event: _EventBase) -> str:
    """Return the wire event-type name for a canonical or ADK-specific event."""
    name = _TYPE_TO_NAME.get(type(event))
    if name is None:
        raise ValueError(f"Unknown event type: {type(event).__name__}")
    return name


def serialize(
    event: _EventBase,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a canonical or ADK-specific event into a ``NewEvent``.

    Caller-supplied metadata is preserved; ``$schema_version`` is always
    stamped last and wins over any caller-supplied value so the wire
    version stays authoritative.
    """
    data = event.model_dump_json(exclude_none=True, by_alias=True).encode("utf-8")
    effective: dict[str, Any] = dict(metadata) if metadata else {}
    effective[SCHEMA_VERSION_METADATA_KEY] = SCHEMA_VERSION
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")
    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> _EventBase | None:
    """Deserialize a ``RecordedEvent`` into a known type, or ``None`` if the
    event type is unregistered *or* the payload cannot be parsed.

    Unknown event types are the reader's "skip" signal: framework-specific
    events from other integrations land here and callers pass them through.
    Parse failures on **known** types (corrupt JSON, schema drift, UTF-8
    errors) are also surfaced as ``None`` + a warning log so a single bad
    event in a long stream cannot crash ``get_session`` and break session
    resume.
    """
    cls = _NAME_TO_TYPE.get(recorded.type)
    if cls is None:
        return None
    try:
        payload = json.loads(recorded.data) if recorded.data else {}
        return cls.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as exc:
        logger.warning(
            "Skipping unparseable event (type=%r, stream=%r, position=%r): %s",
            recorded.type,
            recorded.stream_name,
            recorded.stream_position,
            exc,
        )
        return None


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode metadata JSON, or ``None`` when absent/empty."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd google-adk/python && uv run pytest tests/test_serialization.py -v
```

Expected: every test passes (including all parametrised fixture round-trips).

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/kurrent_google_adk/_serialization.py google-adk/python/tests/test_serialization.py
git commit -m "refactor(adk-python): rewrite _serialization.py over shared registries with v2 stamping (DEV-1533, DEV-1534)"
```

---

## Task 5: Migrate `_codec.py` imports (no behavior change)

Switch imports from `_schema.events` → `kurrent_agent_schema.events` + local `events`. No code logic changes in this task; the thought-part split is Task 6.

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/_codec.py`

- [ ] **Step 1: Replace the imports block**

Open `google-adk/python/kurrent_google_adk/_codec.py`. Replace:

```python
from ._schema.events import (
    ADK_EXTENSION_KEY,
    AgentTransferred,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    Compaction,
    Rewind,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    _EventBase as CanonicalEvent,
)
```

with:

```python
from .events import (
    ADK_EXTENSION_KEY,
    AgentTransferred,
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    Compaction,
    Rewind,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    _EventBase as CanonicalEvent,
)
```

- [ ] **Step 2: Run codec tests, expect them to still pass**

```bash
cd google-adk/python && uv run pytest tests/test_codec.py -v
```

Expected: existing tests pass — `_codec.py` still uses identical Pydantic models with the same field names (the shared `kurrent_agent_schema` events are byte-compatible with the v1 local definitions for ADK-emitted types).

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/kurrent_google_adk/_codec.py
git commit -m "refactor(adk-python): re-point _codec imports to shared schema (DEV-1533)"
```

---

## Task 6: Emit `AssistantThinkingGenerated` for `Part.thought=True` (TDD)

ADK `types.Part(thought=True)` text becomes a separate canonical event instead of bleeding into `AssistantTextGenerated.content`. Round-trip preserves the thought flag.

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/_codec.py`
- Modify: `google-adk/python/tests/test_codec.py`

- [ ] **Step 1: Add failing tests**

Append to `google-adk/python/tests/test_codec.py` (keep all existing tests intact). The new tests use the same imports as the existing file; ensure `AssistantThinkingGenerated` is in the imports from `kurrent_google_adk.events` (or `_schema.events` if the file still uses that — see note below).

> **Note:** `test_codec.py` currently imports from `kurrent_google_adk._schema.events`. As part of this task, change that import line to `from kurrent_google_adk.events import (...)` and ensure `AssistantThinkingGenerated` is included. The set of names imported should match exactly what the file uses today plus `AssistantThinkingGenerated`.

Add at the end of the file:

```python


def test_thought_part_emits_assistant_thinking_generated() -> None:
    # ADK Event with one thought part and one normal text part on a model author.
    adk_event = AdkEvent(
        author="root",
        invocation_id="inv-th-1",
        content=types.Content(
            parts=[
                types.Part(text="planning steps", thought=True),
                types.Part(text="here is the answer"),
            ],
            role="model",
        ),
    )

    canonical = event_to_canonical(adk_event)

    types_emitted = [type(c).__name__ for c in canonical]
    assert "AssistantThinkingGenerated" in types_emitted
    assert "AssistantTextGenerated" in types_emitted

    thinking = next(c for c in canonical if type(c).__name__ == "AssistantThinkingGenerated")
    assert thinking.content == "planning steps"
    assert thinking.encrypted is False
    assert thinking.signature is None

    text = next(c for c in canonical if type(c).__name__ == "AssistantTextGenerated")
    assert text.content == "here is the answer"


def test_text_part_with_thought_false_stays_in_assistant_text() -> None:
    adk_event = AdkEvent(
        author="root",
        invocation_id="inv-th-2",
        content=types.Content(parts=[types.Part(text="just text", thought=False)], role="model"),
    )

    canonical = event_to_canonical(adk_event)
    assert [type(c).__name__ for c in canonical] == ["AssistantTextGenerated"]
    assert canonical[0].content == "just text"


def test_thought_and_tool_call_round_trip() -> None:
    adk_event = AdkEvent(
        author="root",
        invocation_id="inv-th-3",
        content=types.Content(
            parts=[
                types.Part(text="reasoning", thought=True),
                types.Part(text="here is the answer"),
                types.Part(
                    function_call=types.FunctionCall(id="c-1", name="search", args={"q": "x"})
                ),
            ],
            role="model",
        ),
    )

    canonical = event_to_canonical(adk_event)
    rebuilt = canonical_to_events(canonical)
    assert len(rebuilt) == 1
    rebuilt_parts = rebuilt[0].content.parts
    assert any(p.text == "reasoning" and getattr(p, "thought", False) for p in rebuilt_parts)
    assert any(p.text == "here is the answer" and not getattr(p, "thought", False) for p in rebuilt_parts)
    assert any(p.function_call is not None and p.function_call.name == "search" for p in rebuilt_parts)


def test_empty_args_tool_call_round_trip_preserves_empty_dict() -> None:
    adk_event = AdkEvent(
        author="root",
        invocation_id="inv-empty",
        content=types.Content(
            parts=[types.Part(function_call=types.FunctionCall(id="c-1", name="ping", args={}))],
            role="model",
        ),
    )

    canonical = event_to_canonical(adk_event)
    rebuilt = canonical_to_events(canonical)
    assert len(rebuilt) == 1
    fc = rebuilt[0].content.parts[0].function_call
    assert fc is not None
    assert fc.args == {}
```

- [ ] **Step 2: Run codec tests; expect the four new ones to fail**

```bash
cd google-adk/python && uv run pytest tests/test_codec.py -v
```

Expected: the three thought-related tests fail (today the codec collapses thought text into `AssistantTextGenerated`); the empty-args test should already pass.

- [ ] **Step 3: Update `_codec.py` to split thought parts**

Open `google-adk/python/kurrent_google_adk/_codec.py`.

**Edit `_classify_parts`** (around line 251). Replace:

```python
def _classify_parts(
    content: types.Content | None,
) -> tuple[str | None, list[types.FunctionCall], list[types.FunctionResponse]]:
    if content is None or not content.parts:
        return None, [], []
    text_chunks: list[str] = []
    calls: list[types.FunctionCall] = []
    responses: list[types.FunctionResponse] = []
    for part in content.parts:
        if part.text is not None:
            text_chunks.append(part.text)
        if part.function_call is not None:
            calls.append(part.function_call)
        if part.function_response is not None:
            responses.append(part.function_response)
    text = "".join(text_chunks) if text_chunks else None
    return text, calls, responses
```

with:

```python
def _classify_parts(
    content: types.Content | None,
) -> tuple[
    str | None,  # text (thought=False parts only)
    str | None,  # thought (thought=True parts only)
    list[types.FunctionCall],
    list[types.FunctionResponse],
]:
    if content is None or not content.parts:
        return None, None, [], []
    text_chunks: list[str] = []
    thought_chunks: list[str] = []
    calls: list[types.FunctionCall] = []
    responses: list[types.FunctionResponse] = []
    for part in content.parts:
        if part.text is not None:
            if getattr(part, "thought", False):
                thought_chunks.append(part.text)
            else:
                text_chunks.append(part.text)
        if part.function_call is not None:
            calls.append(part.function_call)
        if part.function_response is not None:
            responses.append(part.function_response)
    text = "".join(text_chunks) if text_chunks else None
    thought = "".join(thought_chunks) if thought_chunks else None
    return text, thought, calls, responses
```

**Edit `event_to_canonical`** (around line 113). Find:

```python
    # Conversation content
    text_content, function_calls, function_responses = _classify_parts(event.content)
```

Replace with:

```python
    # Conversation content
    text_content, thought_content, function_calls, function_responses = _classify_parts(event.content)
```

Then immediately before the `if event.author == "user":` branch (around line 132), add this block to emit thinking events for non-user authors:

```python
    if event.author != "user" and thought_content is not None:
        results.append(
            AssistantThinkingGenerated(
                content=thought_content,
                encrypted=False,
                signature=None,
                message_id=event.id,
                author_name=event.author,
                message_index=0,
                timestamp=timestamp,
                extensions=extensions,
            )
        )
```

**Edit `_reconstruct_one`** (around line 405). Find the assistant-text branch:

```python
        elif isinstance(event, AssistantTextGenerated):
            author = event.author_name or author
            if event.content is not None:
                parts.append(types.Part(text=event.content))
```

and add an `AssistantThinkingGenerated` branch immediately above it (so thought parts come first when both are present in the same group):

```python
        elif isinstance(event, AssistantThinkingGenerated):
            author = event.author_name or author
            if event.content is not None:
                parts.append(types.Part(text=event.content, thought=True))
```

- [ ] **Step 4: Run codec tests; expect all to pass**

```bash
cd google-adk/python && uv run pytest tests/test_codec.py -v
```

Expected: all tests pass, including the three new thought-related tests and the empty-args lock-in.

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/kurrent_google_adk/_codec.py google-adk/python/tests/test_codec.py
git commit -m "feat(adk-python): emit AssistantThinkingGenerated for Part.thought=True (DEV-1533)"
```

---

## Task 7: Migrate service-module imports

`session_service.py`, `memory_service.py`, `artifact_service.py` still import from `_schema/`. Re-point them at `kurrent_google_adk.events` and the unchanged `_streams.py` (function names didn't change). `USAGE_METADATA_KEY` should come from the shared package.

**Files:**
- Modify: `google-adk/python/kurrent_google_adk/session_service.py`
- Modify: `google-adk/python/kurrent_google_adk/memory_service.py`
- Modify: `google-adk/python/kurrent_google_adk/artifact_service.py`

- [ ] **Step 1: Update `session_service.py` imports**

Open `google-adk/python/kurrent_google_adk/session_service.py`. Replace:

```python
from . import _serialization
from ._codec import canonical_to_events, event_to_canonical, extract_usage_metadata
from ._revisions import RevisionTracker, SessionKey, StaleSessionError
from ._schema import events as _events
from ._schema.events import ADK_EXTENSION_KEY
from ._schema.stream_names import for_session
```

with:

```python
from kurrent_agent_schema.usage import USAGE_METADATA_KEY

from . import _serialization, events as _events
from ._codec import canonical_to_events, event_to_canonical, extract_usage_metadata
from ._revisions import RevisionTracker, SessionKey, StaleSessionError
from ._streams import for_session
from .events import ADK_EXTENSION_KEY
```

Then **delete** the local definition of `USAGE_METADATA_KEY` near the top of the file (the line `USAGE_METADATA_KEY = "$usage"` at around line 43); the value now comes from the shared package.

- [ ] **Step 2: Update `memory_service.py` imports**

Open `google-adk/python/kurrent_google_adk/memory_service.py`. Replace:

```python
from . import _serialization
from ._schema import events as _events
from ._schema.events import ADK_EXTENSION_KEY
from ._schema.stream_names import for_memory
```

with:

```python
from . import _serialization, events as _events
from ._streams import for_memory
from .events import ADK_EXTENSION_KEY
```

- [ ] **Step 3: Update `artifact_service.py` imports**

Open `google-adk/python/kurrent_google_adk/artifact_service.py`. Replace:

```python
from . import _serialization
from ._schema import events as _events
from ._schema.stream_names import for_artifact
```

with:

```python
from . import _serialization, events as _events
from ._streams import for_artifact
```

- [ ] **Step 4: Run service-level tests**

```bash
cd google-adk/python && uv run pytest tests/test_session_service.py tests/test_memory_service.py tests/test_artifact_service.py -v
```

Expected: all tests pass. Some tests may need Docker (kurrentdb container fixture); skip with `-k "not integration"` if Docker isn't available locally — the CI run will execute the integration tier.

- [ ] **Step 5: Run the full test suite**

```bash
cd google-adk/python && uv run pytest tests/ -v
```

Expected: all tests pass (excluding any tests that genuinely require KurrentDB if Docker is unavailable).

- [ ] **Step 6: Commit**

```bash
git add google-adk/python/kurrent_google_adk/session_service.py google-adk/python/kurrent_google_adk/memory_service.py google-adk/python/kurrent_google_adk/artifact_service.py
git commit -m "refactor(adk-python): re-point service imports to shared schema + new events module (DEV-1533)"
```

---

## Task 8: Add `usage_metadata` re-hydration regression test

Lock in commit `de2c3eb`'s fix: `get_session` must re-populate `Event.usage_metadata` from the `$usage` metadata stamped on append. Append an event with usage, read it back, assert the metadata round-trips.

**Files:**
- Modify: `google-adk/python/tests/test_session_service.py`

- [ ] **Step 1: Add the regression test**

Append to `google-adk/python/tests/test_session_service.py`. The test uses the existing `kurrentdb_client` fixture from `conftest.py`.

```python


@pytest.mark.asyncio
async def test_get_session_rehydrates_usage_metadata(kurrentdb_client) -> None:
    """Regression for commit de2c3eb (DEV-1479): $usage metadata stamped on
    append must round-trip back into ``Event.usage_metadata`` on read."""
    from google.adk.events.event import Event as AdkEvent
    from google.genai import types

    from kurrent_google_adk.session_service import KurrentDBSessionService

    service = KurrentDBSessionService(kurrentdb_client)
    session = await service.create_session(
        app_name="testapp",
        user_id="alice",
        session_id="usage-regression",
        state={},
    )

    assistant_event = AdkEvent(
        author="root",
        invocation_id="inv-usage-1",
        content=types.Content(
            parts=[types.Part(text="answer with thinking")],
            role="model",
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=42,
            candidates_token_count=17,
            total_token_count=59,
            cached_content_token_count=8,
            thoughts_token_count=5,
        ),
    )
    await service.append_event(session, assistant_event)

    fetched = await service.get_session(
        app_name="testapp",
        user_id="alice",
        session_id="usage-regression",
    )
    assert fetched is not None
    persisted = next(e for e in fetched.events if e.invocation_id == "inv-usage-1")
    assert persisted.usage_metadata is not None
    assert persisted.usage_metadata.prompt_token_count == 42
    assert persisted.usage_metadata.candidates_token_count == 17
    assert persisted.usage_metadata.total_token_count == 59
    assert persisted.usage_metadata.cached_content_token_count == 8
    assert persisted.usage_metadata.thoughts_token_count == 5
```

> **Note on imports:** if `pytest` isn't already imported at the top of the file, add `import pytest`. If `kurrentdb_client` fixture isn't already in scope (it's surfaced through `conftest.py`), nothing further is needed.

- [ ] **Step 2: Run the new test**

```bash
cd google-adk/python && uv run pytest tests/test_session_service.py::test_get_session_rehydrates_usage_metadata -v
```

Expected: PASS (Docker required for the KurrentDB container fixture).

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/tests/test_session_service.py
git commit -m "test(adk-python): lock in usage_metadata re-hydration on get_session (DEV-1534)"
```

---

## Task 9: Remove the legacy `_schema/` module and its tests

Now that no code references `_schema/`, delete the whole subpackage and its dedicated test file.

**Files:**
- Delete: `google-adk/python/kurrent_google_adk/_schema/__init__.py`
- Delete: `google-adk/python/kurrent_google_adk/_schema/events.py`
- Delete: `google-adk/python/kurrent_google_adk/_schema/stream_names.py`
- Delete: `google-adk/python/kurrent_google_adk/_schema/` directory
- Delete: `google-adk/python/tests/test_schema.py`

- [ ] **Step 1: Confirm no references remain**

```bash
cd google-adk/python && grep -rn "from kurrent_google_adk._schema\|from \._schema\|kurrent_google_adk\._schema" kurrent_google_adk tests
```

Expected: no matches. If any remain, fix them before deleting.

- [ ] **Step 2: Delete files**

```bash
rm google-adk/python/kurrent_google_adk/_schema/__init__.py
rm google-adk/python/kurrent_google_adk/_schema/events.py
rm google-adk/python/kurrent_google_adk/_schema/stream_names.py
rmdir google-adk/python/kurrent_google_adk/_schema
rm google-adk/python/tests/test_schema.py
```

- [ ] **Step 3: Run the full test suite**

```bash
cd google-adk/python && uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add -u google-adk/python/kurrent_google_adk/_schema google-adk/python/tests/test_schema.py
git commit -m "refactor(adk-python): drop legacy _schema/ subpackage (DEV-1533)"
```

---

## Task 10: Update `google-adk/python/DESIGN.md`

**Files:**
- Modify: `google-adk/python/DESIGN.md`

- [ ] **Step 1: Read the file to locate sections**

```bash
sed -n '1,120p' google-adk/python/DESIGN.md
```

- [ ] **Step 2: Apply the documentation changes**

Make the following edits:

1. **Top of file** — every reference to `SCHEMA.md` becomes `SCHEMA_v2.md`. Search and replace `schema/SCHEMA.md` → `schema/SCHEMA_v2.md` in the document. Same for any `SCHEMA.md §X` references — bump to `SCHEMA_v2.md §X`; the section numbers for canonical content are stable in v2 except where v2 promotes new events.

2. **Package layout** — find the section that currently shows the `_schema/` subpackage in the file tree (around line 50-60) and replace the layout snippet with the post-migration layout:

```
kurrent_google_adk/
├── __init__.py
├── _codec.py                  # ADK Event ↔ canonical decomposition
├── _revisions.py              # RevisionTracker + SessionKey
├── _serialization.py          # canonical-event ↔ KurrentDB wire shape
├── _streams.py                # stream-name builders
├── events.py                  # ADK-specific event types + canonical re-exports
├── artifact_service.py
├── client.py
├── credential_service.py
├── memory_service.py
├── session_service.py
└── ...
```

3. **Schema-vendoring paragraph** — find the paragraph that says "The `_schema/` subpackage is initially vendored — the same Pydantic models the AFW-Python integration defines. Once a shared `kurrent-agent-schema` package exists (see repo-structure discussion below), `_schema` becomes a thin re-export of that package." (around line 80). Replace it with:

```markdown
Canonical event types come from the shared `kurrent-agent-schema` Python
package. ADK-specific event types (`AgentTransferred`, `Rewind`,
`Compaction`, `StateDelta`) live in `events.py` and are registered in
`_serialization.py` alongside the shared canonical set. The
`extensions.adk` slug owns ADK-specific fields per `SCHEMA_v2.md §5.3`.
```

4. **Codec section** — locate §5 (codec mapping rules). Add a paragraph at the end of the section:

```markdown
**Thought parts.** ADK's `types.Part` carries a `thought: bool` flag.
When `thought=True`, the part's text is emitted as
`AssistantThinkingGenerated` with `encrypted=False` and `signature=None`
(ADK exposes plaintext thinking and has no signing concept). The thinking
event shares `extensions.adk.id` with any sibling assistant text/tool
events from the same source ADK `Event`, so `_reconstruct_one`'s grouping
re-merges them on read; the round-trip restores `Part(text=..., thought=True)`.
This was a v1 bug — thought text silently bled into
`AssistantTextGenerated.content` — fixed by SCHEMA_v2's promotion of
thinking to canonical (§3.2).
```

5. **Stream layout section** — find the table or list documenting stream names. Update the framework-specific entries:

| Stream | Owner | Purpose |
|---|---|---|
| `AgentSession-{session_id}` | shared | Primary conversation. |
| `AgentMemory-{app_name}-{user_id}` | shared | Per-app, per-user retained facts. |
| `AgentArtifact-{app}-{user}-[{session}-]{file}` | shared | Artifact versions. |
| `AppState-{app_name}` | ADK | App-scoped state (`app:` prefix keys). **Renamed from v1 `AgentAppState-`.** |
| `UserState-{app_name}-{user_id}` | ADK | User-scoped state. **Renamed from v1 `AgentUserState-`.** |
| `Credentials-{app_name}-{user_id}` | ADK | Tool OAuth credentials. **Renamed from v1 `AgentCredentials-`.** |

Add a note: "Library was unshipped at the time of the v1→v2 rename, so no migration concern."

6. **Add an `extensions.adk` slug section** — if not already present, add at the end of the file (or in §5/§6 wherever extensions are discussed):

```markdown
### Extension slug ownership

ADK owns the `extensions.adk` slug per `SCHEMA_v2.md §5.3`. ADK-specific
fields (source `Event.id`, `invocation_id`, `branch`, `partial`,
`long_running_tool_ids`, the full `EventActions` payload) are preserved
under this slug so same-framework round-trip is lossless and cross-
framework readers can ignore the slug entirely.
```

- [ ] **Step 3: Sanity-check the edited file**

```bash
grep -n "SCHEMA_v2\|AppState-\|UserState-\|Credentials-\|extensions.adk\|AssistantThinkingGenerated" google-adk/python/DESIGN.md | head -30
```

Expected: matches throughout, demonstrating the renames stuck.

- [ ] **Step 4: Commit**

```bash
git add google-adk/python/DESIGN.md
git commit -m "docs(adk-python): update DESIGN.md for schema v2 + thinking emission (DEV-1535)"
```

---

## Task 11: Update `google-adk/python/README.md`

**Files:**
- Modify: `google-adk/python/README.md`

- [ ] **Step 1: Read current README**

```bash
cat google-adk/python/README.md
```

- [ ] **Step 2: Apply edits**

1. Replace any link to `SCHEMA.md` with `SCHEMA_v2.md`.
2. In the section discussing how the integration encodes events, add (or update) a paragraph to read:

```markdown
Canonical events come from the shared `kurrent-agent-schema` Python
package. ADK-specific event types (`AgentTransferred`, `Rewind`,
`Compaction`, `StateDelta`) are added locally and serialised alongside
the canonical set. Token usage rides on the canonical `$usage` event
metadata key (shape: `kurrent_agent_schema.usage.TokenUsage`).
```

3. If the README enumerates ADK-owned stream names, update them to v2 names (`AppState-`, `UserState-`, `Credentials-`).

4. Drop any v1-only language (e.g. "vendored Pydantic models", references to `_schema/`).

- [ ] **Step 3: Commit**

```bash
git add google-adk/python/README.md
git commit -m "docs(adk-python): refresh README for schema v2 (DEV-1535)"
```

---

## Task 12: Update monorepo `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md` (repo root)

- [ ] **Step 1: Find the per-integration table**

```bash
sed -n '1,40p' CLAUDE.md
```

The table currently has a row for "Google ADK (Python)" pointing at `google-adk/python/DESIGN.md` with storage style "verbatim `Event` with state-scope routing".

- [ ] **Step 2: Update the storage description**

Replace that row's storage cell with:

> verbatim `Event` with state-scope routing (app / user / session streams); canonical types from shared `kurrent-agent-schema` (Python)

- [ ] **Step 3: If the file documents a v1→v2 stream-prefix change for ADK**, add an entry to the gotchas list (after the Strands tool-result-status item):

```markdown
- **ADK-owned stream prefixes dropped the `Agent-` prefix in v2.** `AgentAppState-` → `AppState-`, `AgentUserState-` → `UserState-`, `AgentCredentials-` → `Credentials-`, per `SCHEMA_v2.md §2.2`. Library was unshipped, so no migration concern; readers querying old prefixes will not find new streams. See DEV-1528.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(monorepo): note ADK schema v2 in per-integration table (DEV-1535)"
```

---

## Task 13: Final verification + push

- [ ] **Step 1: Run the full test suite end-to-end**

```bash
cd google-adk/python && uv run pytest tests/ -v
```

Expected: every test passes. If Docker is unavailable, integration tests that require the `kurrentdb_container` fixture may skip; that's expected locally and CI will run them.

- [ ] **Step 2: Run ruff**

```bash
cd google-adk/python && uv run ruff check kurrent_google_adk tests
```

Expected: clean exit, no warnings.

- [ ] **Step 3: Confirm `_schema/` is gone**

```bash
test -d google-adk/python/kurrent_google_adk/_schema && echo "STILL EXISTS — fix" || echo "removed"
```

Expected: `removed`.

- [ ] **Step 4: Confirm shared schema is the source of truth**

```bash
grep -rn "from kurrent_agent_schema\|kurrent_agent_schema\." google-adk/python/kurrent_google_adk
```

Expected: import lines in `_serialization.py`, `_streams.py`, `events.py`, `session_service.py`.

- [ ] **Step 5: Push the branch**

```bash
git push -u origin alexeyzimarev/dev-1528-google-adk-migrate-to-canonical-schema-v2
```

- [ ] **Step 6: Open the PR**

```bash
gh pr create --title "refactor(adk-python): migrate to canonical schema v2 (DEV-1528)" --body "$(cat <<'EOF'
## Summary

- Replace local `_schema/` Pydantic models with the shared `kurrent-agent-schema` (Python) package.
- Rename ADK-owned framework-specific stream prefixes per `SCHEMA_v2.md §2.2`: `AgentAppState-` → `AppState-`, `AgentUserState-` → `UserState-`, `AgentCredentials-` → `Credentials-`.
- Stamp `$schema_version=2` on every serialised event's metadata; harden `deserialize` to log + skip unparseable events.
- Emit `AssistantThinkingGenerated` for `Part.thought=True` (v2 promotes thinking to canonical; fixes a v1 bug where thought text bled into `AssistantTextGenerated`).
- Round-trip test against `schema/fixtures/events/*.json` + `metadata/usage.json`.
- Lock in the two known gotchas (`ff1540d` empty-args, `de2c3eb` `usage_metadata` re-hydration).

Single PR covering DEV-1533 (code), DEV-1534 (fixture tests), DEV-1535 (docs).

## Test plan

- [ ] `uv run pytest google-adk/python/tests/ -v` passes locally.
- [ ] CI integration suite (with Docker) passes.
- [ ] Manual: run the ADK sample and confirm session resume preserves usage and thinking.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

## Self-review

**Spec coverage check:**

- ✅ `_schema/` deletion → Task 9.
- ✅ Shared `kurrent-agent-schema` dependency → Task 1.
- ✅ Local `events.py` for ADK-specific types → Task 2.
- ✅ `_streams.py` with v2 prefixes for ADK-owned streams → Task 3.
- ✅ `_serialization.py` thin adapter + `$schema_version` stamping + hardened deserialize → Task 4.
- ✅ Codec import migration → Task 5.
- ✅ `AssistantThinkingGenerated` emission for `Part.thought=True` → Task 6.
- ✅ Service-module import migration → Task 7.
- ✅ `usage_metadata` re-hydration regression test → Task 8.
- ✅ Empty-args lock-in → Task 6 (last test).
- ✅ Fixture round-trip tests → Task 4 (parametrised over `schema/fixtures/events/*.json`).
- ✅ DESIGN.md → Task 10.
- ✅ README.md → Task 11.
- ✅ Monorepo CLAUDE.md → Task 12.
- ✅ Full verification + PR → Task 13.

**Type consistency check:**

- `AssistantThinkingGenerated` field set used in tests (`content`, `encrypted`, `signature`, `message_id`, `author_name`, `message_index`, `timestamp`, `extensions`) matches the shared schema (`schema/python/kurrent_agent_schema/events.py:147-163`).
- `_serialization._NAME_TO_TYPE` exposed for the fixture parametrise; used as a private-API hook in tests, intentional.
- `for_*` function names in `_streams.py` unchanged; service modules re-import them via `from ._streams import for_*`.
- `USAGE_METADATA_KEY` now sourced from `kurrent_agent_schema.usage` everywhere; the local definition in `session_service.py` is removed in Task 7 Step 1.

**Placeholder scan:** no TBDs, TODOs, or "implement later" stubs.
