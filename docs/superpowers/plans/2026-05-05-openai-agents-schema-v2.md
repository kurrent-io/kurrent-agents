# OpenAI Agents — Canonical Schema v2 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the OpenAI Agents Python integration to the shared `kurrent-agent-schema` (v0.4.0, Protobuf Edition 2024), folding in the canonical promotions for assistant thinking and MCP tool-approval interrupts.

**Architecture:** Replace the hand-rolled Pydantic schema in `_schema/` with imports from `kurrent_agent_schema`; keep the framework-specific `OpenAIItem` wrapper as Pydantic in a new `_openai_events.py` (mirrors Strands' `_strands_events.py`). The codec decomposes Responses API items into protobuf canonical events with `extensions.openai.raw_item` preserved verbatim under a `google.protobuf.Struct`. New mappings: `reasoning` → `AssistantThinkingGenerated`, `mcp_approval_request`/`mcp_approval_response` → `InterruptIssued`/`InterruptResolved`. Handoffs stay deferred (DEV-1684).

**Tech Stack:** Python 3.11+, `kurrent-agent-schema >= 0.3.0` (protobuf), `protobuf >= 6.32.1, < 8`, `pydantic >= 2.5` (for `OpenAIItem` only), `kurrentdbclient >= 1.2.0`, `openai-agents >= 0.2.0`.

**Linear:** DEV-1530 (umbrella), DEV-1539 (migration), DEV-1540 (fixture round-trip), DEV-1541 (docs). DEV-1684 (handoffs) is out of scope.

---

## File Structure

**Create:**
- `openai-agents/python/kurrent_openai_agents/_openai_events.py` — Pydantic `OpenAIItem` (framework-specific wrapper), mirrors `strands/python/kurrent_strands/_strands_events.py`.
- `openai-agents/python/kurrent_openai_agents/_stream_names.py` — local stream-name builder with id normalisation; thin wrapper over shared `agent_session_stream`.
- `openai-agents/python/tests/test_fixtures_round_trip.py` — fixture-driven drift detection.

**Modify:**
- `openai-agents/python/pyproject.toml` — add `kurrent-agent-schema`, `protobuf`, drop unneeded surface; add uv source.
- `openai-agents/python/kurrent_openai_agents/_codec.py` — full rewrite for proto canonical events + new mappings.
- `openai-agents/python/kurrent_openai_agents/_serialization.py` — full rewrite to mirror Strands two-track adapter.
- `openai-agents/python/kurrent_openai_agents/session.py` — switch imports, lifecycle skip-list, schema-version stamp.
- `openai-agents/python/kurrent_openai_agents/__init__.py` — re-export `OpenAIItem` from new location if currently exported.
- `openai-agents/python/tests/test_codec.py` — update for proto types + new mappings.
- `openai-agents/python/tests/test_session.py` — extend with thinking + MCP cases.
- `openai-agents/python/DESIGN.md` — reference SCHEMA_v2; document thinking + interrupt mappings; note DEV-1684 as the handoff follow-up.
- `openai-agents/python/README.md` — short refresh.
- `CLAUDE.md` (repo root) — refresh OpenAI Agents row in the per-integration table.

**Delete:**
- `openai-agents/python/kurrent_openai_agents/_schema/__init__.py`
- `openai-agents/python/kurrent_openai_agents/_schema/events.py`
- `openai-agents/python/kurrent_openai_agents/_schema/stream_names.py`

---

## Phase 1 — Schema Migration (DEV-1539)

### Task 1: Add the shared schema dependency

**Files:**
- Modify: `openai-agents/python/pyproject.toml`

- [ ] **Step 1: Update `pyproject.toml` dependencies**

Replace the current `[project] dependencies` block and add a `[tool.uv.sources]` entry. The full file should read:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kurrent-openai-agents"
version = "0.0.1"
description = "KurrentDB integration for the OpenAI Agents SDK (Python)"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "Kurrent, Inc." }]
keywords = ["kurrentdb", "eventsourcing", "openai-agents", "llm", "agents"]
dependencies = [
  "openai-agents >= 0.2.0",
  "kurrent-agent-schema >= 0.3.0",
  "kurrentdbclient >= 1.2.0",
  "protobuf >= 6.32.1, < 8",
  "pydantic >= 2.5",
]

[project.optional-dependencies]
dev = [
  "pytest >= 8",
  "pytest-asyncio >= 0.23",
  "ruff >= 0.6",
  "kurrent-agents-testing",
]

[tool.hatch.build.targets.wheel]
packages = ["kurrent_openai_agents"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 120
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "RUF"]

[tool.uv.sources]
kurrent-agent-schema = { path = "../../schema/python", editable = true }
kurrent-agents-testing = { path = "../../testing", editable = true }
```

The `>= 0.3.0` floor matches Strands; the embedded `ValidateProtobufRuntimeVersion(6, 32, 1, ...)` in the schema's gencode requires `protobuf >= 6.32.1`. Pinning the floor explicitly here keeps install resolution honest.

- [ ] **Step 2: Sync the lockfile**

Run: `cd openai-agents/python && uv sync --extra dev`
Expected: Resolution succeeds; `uv.lock` updates with `kurrent-agent-schema 0.4.0` (or whatever the local source resolves to) and `protobuf >= 6.32.1`. Verify `python -c "import kurrent_agent_schema; print(kurrent_agent_schema.SCHEMA_VERSION)"` prints `2`.

- [ ] **Step 3: Commit**

```bash
git add openai-agents/python/pyproject.toml openai-agents/python/uv.lock
git commit -m "chore(openai-agents): depend on kurrent-agent-schema (DEV-1539)"
```

---

### Task 2: Add the local Pydantic `OpenAIItem` wrapper

**Files:**
- Create: `openai-agents/python/kurrent_openai_agents/_openai_events.py`

- [ ] **Step 1: Write the new framework-events module**

Create `_openai_events.py`:

```python
"""OpenAI-Agents-specific framework events.

These events are persisted alongside canonical events in
``AgentSession-{session_id}`` streams. They are **not** part of the shared
canonical schema (`SCHEMA_v2.md`); cross-framework readers (ADK / MAF /
Strands / Claude SDK) skip them on read.

The shared :mod:`kurrent_agent_schema` package provides the canonical event
types as protobuf messages. Framework-specific events keep using Pydantic
because they are not part of the cross-framework wire contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


OPENAI_EXTENSION_KEY: str = "openai"
"""Slug under which OpenAI-specific fields ride on canonical events'
``extensions`` map. See ``schema/SCHEMA_v2.md §5``."""


class _OpenAIEventBase(BaseModel):
    """Shared config for OpenAI-Agents framework-specific events.

    ``extra="ignore"`` keeps reads forward-compatible. ``frozen`` gives
    value-type semantics. Bytes round-trip as base64 in JSON for parity
    with the v1 canonical events that used the same configuration.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    extensions: dict[str, dict[str, Any]] | None = None
    """Framework-extension envelope. Mirrors canonical events for parity,
    even though OpenAI rarely populates it on its own framework events."""


class OpenAIItem(_OpenAIEventBase):
    """Wraps a non-canonical OpenAI Agents SDK session item verbatim.

    Canonical conversation items (user/assistant text messages, tool calls,
    tool results, reasoning, MCP approvals) are decomposed into shared
    canonical events. This type exists for items that have no canonical
    analogue today — handoff_call/handoff_output (DEV-1684), computer_call,
    shell_call, web_search, etc. — so they still round-trip through
    ``get_items`` / ``add_items`` without loss.
    """

    item_type: str
    """The OpenAI SDK item's ``type`` field."""

    raw_item: dict[str, Any]
    """Full item dict verbatim for lossless reconstruction on ``get_items``."""

    message_index: int
    """Monotonic session-index assigned on write."""

    timestamp: datetime
```

- [ ] **Step 2: Verify it loads**

Run: `cd openai-agents/python && python -c "from kurrent_openai_agents._openai_events import OpenAIItem, OPENAI_EXTENSION_KEY; print(OPENAI_EXTENSION_KEY)"`
Expected: prints `openai`.

- [ ] **Step 3: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_openai_events.py
git commit -m "feat(openai-agents): add Pydantic OpenAIItem framework event (DEV-1539)"
```

---

### Task 3: Add the local stream-name builder

**Files:**
- Create: `openai-agents/python/kurrent_openai_agents/_stream_names.py`

- [ ] **Step 1: Write a failing test for `for_session`**

Add to `openai-agents/python/tests/test_codec.py` (top of file, after imports — we will rewrite the rest of the file in later tasks but this test slot is fine to seed):

```python
def test_for_session_normalises_unsafe_chars() -> None:
    from kurrent_openai_agents._stream_names import for_session
    assert for_session("plain") == "AgentSession-plain"
    assert for_session("ses sion/01") == "AgentSession-ses%20sion%2F01"


def test_for_session_rejects_empty() -> None:
    import pytest
    from kurrent_openai_agents._stream_names import for_session
    with pytest.raises(ValueError):
        for_session("")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd openai-agents/python && pytest tests/test_codec.py::test_for_session_normalises_unsafe_chars -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kurrent_openai_agents._stream_names'`.

- [ ] **Step 3: Implement `_stream_names.py`**

Create `openai-agents/python/kurrent_openai_agents/_stream_names.py`:

```python
"""Stream-name builder for the OpenAI Agents integration.

Wraps :func:`kurrent_agent_schema.agent_session_stream` with id normalisation
so free-form session ids cannot break the ``AgentSession-`` category prefix.
"""

from __future__ import annotations

import urllib.parse

from kurrent_agent_schema import agent_session_stream

_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
_MAX_SEGMENT_LENGTH = 128


def _normalise_id(value: str, *, field: str) -> str:
    if not value:
        raise ValueError(f"{field} cannot be empty")
    encoded = urllib.parse.quote(value, safe=_SAFE_ID_CHARS)
    if len(encoded) > _MAX_SEGMENT_LENGTH:
        raise ValueError(
            f"{field} exceeds {_MAX_SEGMENT_LENGTH}-char segment budget after "
            f"normalisation (got {len(encoded)}): {value!r}"
        )
    return encoded


def for_session(session_id: str) -> str:
    """Primary conversation stream — ``AgentSession-{normalised_session_id}``."""
    return agent_session_stream(_normalise_id(session_id, field="session_id"))
```

- [ ] **Step 4: Verify the test passes**

Run: `cd openai-agents/python && pytest tests/test_codec.py::test_for_session_normalises_unsafe_chars tests/test_codec.py::test_for_session_rejects_empty -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_stream_names.py openai-agents/python/tests/test_codec.py
git commit -m "feat(openai-agents): local stream-name builder over shared package (DEV-1539)"
```

---

### Task 4: Rewrite serialization (proto canonical + Pydantic OpenAIItem)

**Files:**
- Modify (full rewrite): `openai-agents/python/kurrent_openai_agents/_serialization.py`

- [ ] **Step 1: Write the failing test for canonical proto serialisation**

Append to `openai-agents/python/tests/test_codec.py`:

```python
def test_serialize_stamps_schema_version() -> None:
    import json as _json
    from datetime import UTC, datetime as _dt
    from kurrent_agent_schema import UserMessageReceived
    from kurrent_openai_agents import _serialization

    event = UserMessageReceived(message_index=0)
    event.timestamp.FromDatetime(_dt(2026, 5, 5, tzinfo=UTC))
    event.content = "hi"

    new_event = _serialization.serialize(event)

    assert new_event.type == "UserMessageReceived"
    payload = _json.loads(new_event.data)
    assert payload["content"] == "hi"
    metadata = _json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2


def test_serialize_pydantic_openai_item() -> None:
    import json as _json
    from datetime import UTC, datetime as _dt
    from kurrent_openai_agents import _serialization
    from kurrent_openai_agents._openai_events import OpenAIItem

    item = OpenAIItem(
        item_type="computer_call",
        raw_item={"type": "computer_call", "id": "x"},
        message_index=3,
        timestamp=_dt(2026, 5, 5, tzinfo=UTC),
    )
    new_event = _serialization.serialize(item)
    assert new_event.type == "OpenAIItem"
    payload = _json.loads(new_event.data)
    assert payload["item_type"] == "computer_call"
    assert payload["raw_item"] == {"type": "computer_call", "id": "x"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd openai-agents/python && pytest tests/test_codec.py::test_serialize_stamps_schema_version tests/test_codec.py::test_serialize_pydantic_openai_item -v`
Expected: FAIL — `_serialization.serialize` currently expects the old Pydantic `_EventBase`, which won't accept a proto `Message`.

- [ ] **Step 3: Rewrite `_serialization.py`**

Replace the entire file with:

```python
"""Canonical-event ↔ KurrentDB wire serialization.

Routes canonical events through the shared :mod:`kurrent_agent_schema`
package (protobuf-backed, snake_case JSON via the sanctioned :func:`to_json`
/ :func:`from_json` helpers) and OpenAI-Agents-specific framework events
through their local Pydantic models. Stamps ``$schema_version`` on every
event's metadata per ``schema/SCHEMA_v2.md §9``.

Mirrors :mod:`kurrent_strands._serialization` and
:mod:`kurrent_agent_framework.serialization` on the canonical path.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from google.protobuf.message import Message as ProtoMessage
from kurrent_agent_schema import (
    EVENT_TYPE_BY_NAME,
    EVENT_TYPE_NAMES,
    SCHEMA_VERSION,
    from_json,
    to_json,
)
from kurrentdbclient import NewEvent, RecordedEvent
from pydantic import BaseModel

from ._openai_events import OpenAIItem

SCHEMA_VERSION_METADATA_KEY: str = "$schema_version"
"""Metadata key stamped on every event. See ``SCHEMA_v2.md §9``."""

_OPENAI_NAME_TO_TYPE: dict[str, type[BaseModel]] = {
    "OpenAIItem": OpenAIItem,
}
_OPENAI_TYPE_TO_NAME: dict[type[BaseModel], str] = {
    cls: name for name, cls in _OPENAI_NAME_TO_TYPE.items()
}


def _name_for(event: ProtoMessage | BaseModel) -> str:
    if isinstance(event, ProtoMessage):
        name = EVENT_TYPE_NAMES.get(type(event))
        if name is not None:
            return name
    elif isinstance(event, BaseModel):
        name = _OPENAI_TYPE_TO_NAME.get(type(event))
        if name is not None:
            return name
    raise ValueError(f"Unknown event type: {type(event).__name__}")


def _encode_event_data(event: ProtoMessage | BaseModel) -> bytes:
    if isinstance(event, ProtoMessage):
        return to_json(event).encode("utf-8")
    return event.model_dump_json(exclude_none=True).encode("utf-8")


def serialize(
    event: ProtoMessage | BaseModel,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize a canonical or OpenAI-specific event into a KurrentDB ``NewEvent``.

    Caller-supplied metadata is preserved; ``$schema_version`` is always
    stamped last so the wire version stays authoritative.
    """
    data = _encode_event_data(event)

    effective: dict[str, Any] = dict(metadata) if metadata else {}
    effective[SCHEMA_VERSION_METADATA_KEY] = SCHEMA_VERSION
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")

    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )


def deserialize(recorded: RecordedEvent) -> ProtoMessage | BaseModel | None:
    """Deserialize a ``RecordedEvent`` into a canonical proto event or an
    OpenAI-specific Pydantic event. Returns ``None`` for unknown event types
    (forward-compat / cross-framework tolerance)."""
    proto_cls = EVENT_TYPE_BY_NAME.get(recorded.type)
    if proto_cls is not None:
        if not recorded.data:
            return proto_cls()
        return from_json(proto_cls, recorded.data.decode("utf-8"))

    pydantic_cls = _OPENAI_NAME_TO_TYPE.get(recorded.type)
    if pydantic_cls is not None:
        payload = json.loads(recorded.data) if recorded.data else {}
        return pydantic_cls.model_validate(payload)

    return None


def read_metadata(recorded: RecordedEvent) -> dict[str, Any] | None:
    """Decode KurrentDB event metadata JSON, or ``None`` when absent/invalid."""
    if not recorded.metadata:
        return None
    try:
        return json.loads(recorded.metadata)
    except (json.JSONDecodeError, TypeError):
        return None
```

- [ ] **Step 4: Run the new tests**

Run: `cd openai-agents/python && pytest tests/test_codec.py::test_serialize_stamps_schema_version tests/test_codec.py::test_serialize_pydantic_openai_item -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_serialization.py openai-agents/python/tests/test_codec.py
git commit -m "refactor(openai-agents): serialization on shared schema + \$schema_version stamp (DEV-1539)"
```

---

### Task 5: Rewrite the codec — basic mappings on proto events

The codec rewrite is large enough that we split it across three tasks. Task 5 covers the existing four mappings (`message`, `function_call`, `function_call_output`, fallback `OpenAIItem`) translated to proto. Task 6 adds `reasoning`. Task 7 adds MCP approvals.

**Files:**
- Modify (full rewrite): `openai-agents/python/kurrent_openai_agents/_codec.py`
- Modify: `openai-agents/python/tests/test_codec.py`

- [ ] **Step 1: Update existing codec tests for proto types**

Replace the import block and `TS` constant at the top of `tests/test_codec.py` with:

```python
"""Unit tests for the OpenAI session-item ↔ canonical codec."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from google.protobuf.json_format import MessageToDict
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    InterruptIssued,
    InterruptResolved,
    ToolResultReceived,
    UserMessageReceived,
)

from kurrent_openai_agents import _serialization
from kurrent_openai_agents._codec import canonical_to_items, items_to_canonical
from kurrent_openai_agents._openai_events import OPENAI_EXTENSION_KEY, OpenAIItem


TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _ext(event) -> dict:
    if OPENAI_EXTENSION_KEY not in event.extensions:
        return {}
    return MessageToDict(
        event.extensions[OPENAI_EXTENSION_KEY], preserving_proto_field_name=True
    )
```

Replace the existing `TestCanonicalMapping`, `TestNonCanonicalItems`, and `TestRoundTrip` classes with proto-aware versions:

```python
class TestCanonicalMapping:
    def test_user_text_message(self) -> None:
        items = [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert len(events) == 1
        assert isinstance(events[0], UserMessageReceived)
        assert events[0].content == "hello"
        assert events[0].message_index == 0
        # raw_item preserved verbatim under extensions.openai
        ext = _ext(events[0])
        assert ext["raw_item"] == items[0]
        assert ext["item_type"] == "message"

    def test_assistant_text_message(self) -> None:
        items = [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi there"}],
        }]
        events = items_to_canonical(items, start_index=5, timestamp=TS)
        assert isinstance(events[0], AssistantTextGenerated)
        assert events[0].content == "hi there"
        assert events[0].message_index == 5

    def test_function_call(self) -> None:
        items = [{
            "type": "function_call",
            "call_id": "c1",
            "name": "search",
            "arguments": '{"q": "kurrent"}',
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], AssistantToolCallsGenerated)
        tc = events[0].tool_calls[0]
        assert tc.call_id == "c1"
        assert tc.tool_name == "search"
        assert MessageToDict(tc.arguments, preserving_proto_field_name=True) == {"q": "kurrent"}

    def test_function_call_empty_arguments(self) -> None:
        """Empty-dict args must round-trip as ``{}`` (not collapse to None) — schema commit ff1540d."""
        items = [{
            "type": "function_call",
            "call_id": "c1",
            "name": "list_notes",
            "arguments": "{}",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        tc = events[0].tool_calls[0]
        assert tc.HasField("arguments")
        assert MessageToDict(tc.arguments, preserving_proto_field_name=True) == {}

    def test_function_call_output(self) -> None:
        items = [{
            "type": "function_call_output",
            "call_id": "c1",
            "output": "found 3 hits",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], ToolResultReceived)
        assert events[0].call_id == "c1"
        assert events[0].result == "found 3 hits"


class TestNonCanonicalItems:
    def test_handoff_call_stays_as_openai_item(self) -> None:
        items = [{
            "type": "handoff_call",
            "call_id": "h1",
            "name": "handoff_to_expert",
            "arguments": "{}",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], OpenAIItem)
        assert events[0].item_type == "handoff_call"
        assert events[0].raw_item == items[0]

    def test_unknown_type_stays_as_openai_item(self) -> None:
        items = [{"type": "shell_call", "call_id": "s1", "command": "ls"}]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], OpenAIItem)
        assert events[0].item_type == "shell_call"


class TestRoundTrip:
    def test_simple_conversation_round_trip(self) -> None:
        items = [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "hello"}]},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "hi"}]},
            {"type": "function_call", "call_id": "c1", "name": "search",
             "arguments": '{"q": "x"}'},
            {"type": "function_call_output", "call_id": "c1", "output": '{"hits": 3}'},
        ]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        restored = canonical_to_items(events)
        assert restored == items

    def test_cross_framework_fallback(self) -> None:
        """When no raw_item extension is present, we rebuild a minimal item."""
        evt = UserMessageReceived(message_index=0)
        evt.content = "hello"
        evt.timestamp.FromDatetime(TS.replace(tzinfo=None))
        items = canonical_to_items([evt])
        assert items == [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd openai-agents/python && pytest tests/test_codec.py::TestCanonicalMapping tests/test_codec.py::TestRoundTrip -v`
Expected: FAILS — codec still imports from `_schema.events` which is the Pydantic version.

- [ ] **Step 3: Rewrite `_codec.py` for the basic mappings**

Replace the entire file with:

```python
"""OpenAI Agents SDK ``TResponseInputItem`` ↔ canonical event mapping.

The SDK's session items are OpenAI Responses API shapes (TypedDicts with a
discriminated ``type`` field). We decompose each item into either a canonical
event (``UserMessageReceived`` / ``AssistantTextGenerated`` /
``AssistantToolCallsGenerated`` / ``ToolResultReceived`` /
``AssistantThinkingGenerated`` / ``InterruptIssued`` / ``InterruptResolved``)
when the shape maps cleanly, or a framework-specific ``OpenAIItem`` event
carrying the raw dict verbatim when it doesn't.

Every emitted canonical event also stashes the original item under
``extensions.openai.raw_item`` — so reconstruction via
``canonical_to_items`` is lossless regardless of whether the item was
mapped or verbatim-stored.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtoMessage
from google.protobuf.struct_pb2 import Struct
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    InterruptIssued,
    InterruptResolved,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)

from ._openai_events import OPENAI_EXTENSION_KEY, OpenAIItem

logger = logging.getLogger("kurrent_openai_agents._codec")


# OpenAI item types that we decompose onto canonical events. Tasks 6 and 7
# extend this set.
_CANONICAL_ITEM_TYPES = frozenset({
    "message",
    "function_call",
    "function_call_output",
})


def items_to_canonical(
    items: list[dict[str, Any]],
    *,
    start_index: int,
    timestamp: datetime | None = None,
) -> list[ProtoMessage | OpenAIItem]:
    """Decompose a list of OpenAI session items into canonical events.

    ``start_index`` is the session-wide monotonic counter assigned by the
    caller. Each item gets its own index; canonical events emitted for one
    item share that index.
    """
    ts = timestamp or datetime.now(UTC)
    results: list[ProtoMessage | OpenAIItem] = []

    for offset, item in enumerate(items):
        message_index = start_index + offset
        kind = item.get("type", "message")

        if kind == "message":
            results.extend(_map_message(item, message_index, ts))
        elif kind == "function_call":
            results.append(_map_function_call(item, message_index, ts))
        elif kind == "function_call_output":
            results.append(_map_function_call_output(item, message_index, ts))
        else:
            # Non-canonical — handoff_*, computer_call, shell_call, web_search,
            # etc. Reasoning + MCP approvals are added in tasks 6 and 7.
            results.append(_wrap_openai_item(item, kind, message_index, ts))

    return results


def canonical_to_items(events: list[ProtoMessage | OpenAIItem]) -> list[dict[str, Any]]:
    """Reconstruct OpenAI session items from an ordered event stream.

    Prefers ``extensions.openai.raw_item`` for lossless reconstruction.
    Falls back to rebuilding from canonical fields when the event carries no
    extension (e.g. cross-framework reads of a session written by ADK).
    """
    items: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, OpenAIItem):
            items.append(dict(event.raw_item))
            continue
        ext = _read_openai_extension(event)
        raw = ext.get("raw_item") if isinstance(ext, dict) else None
        if isinstance(raw, dict) and raw:
            items.append(dict(raw))
            continue
        item = _fallback_reconstruct(event)
        if item is not None:
            items.append(item)
    return items


# ----- per-item-type mappers -------------------------------------------------


def _map_message(
    item: dict[str, Any], message_index: int, ts: datetime
) -> list[ProtoMessage]:
    role = item.get("role", "user")
    content = _extract_message_text(item.get("content"))

    if role == "user":
        evt = UserMessageReceived(message_index=message_index)
    else:
        # Assistant / system — map to AssistantTextGenerated.
        evt = AssistantTextGenerated(message_index=message_index)

    if content is not None:
        evt.content = content
    evt.timestamp.FromDatetime(ts.astimezone(UTC).replace(tzinfo=None))
    _set_openai_extension(evt, {"raw_item": dict(item), "item_type": item.get("type", "message")})
    return [evt]


def _map_function_call(
    item: dict[str, Any], message_index: int, ts: datetime
) -> AssistantToolCallsGenerated:
    evt = AssistantToolCallsGenerated(message_index=message_index)
    evt.timestamp.FromDatetime(ts.astimezone(UTC).replace(tzinfo=None))

    tc = ToolCallInfo()
    tc.call_id = item.get("call_id") or ""
    tc.tool_name = item.get("name") or ""
    args = _parse_arguments(item.get("arguments"))
    if args is not None:
        # Empty-dict args are preserved by design (schema commit ff1540d).
        # ``Struct.update({})`` does NOT set the has-bit; route through
        # MergeFrom on a fresh Struct so ``HasField("arguments") == True``
        # even when args is an empty dict — distinguishing "explicitly empty"
        # from "absent".
        s = Struct()
        s.update(args)
        tc.arguments.MergeFrom(s)
    evt.tool_calls.append(tc)

    _set_openai_extension(evt, {"raw_item": dict(item), "item_type": "function_call"})
    return evt


def _map_function_call_output(
    item: dict[str, Any], message_index: int, ts: datetime
) -> ToolResultReceived:
    evt = ToolResultReceived(
        call_id=item.get("call_id") or "",
        message_index=message_index,
    )
    result = _serialize_output(item.get("output"))
    if result is not None:
        evt.result = result
    evt.timestamp.FromDatetime(ts.astimezone(UTC).replace(tzinfo=None))
    _set_openai_extension(evt, {"raw_item": dict(item), "item_type": "function_call_output"})
    return evt


def _wrap_openai_item(
    item: dict[str, Any], kind: str, message_index: int, ts: datetime
) -> OpenAIItem:
    return OpenAIItem(
        item_type=kind,
        raw_item=dict(item),
        message_index=message_index,
        timestamp=ts,
    )


# ----- extension helpers -----------------------------------------------------


def _set_openai_extension(event: ProtoMessage, payload: dict[str, Any]) -> None:
    """Stamp ``event.extensions['openai']`` from a plain dict.

    Goes through :func:`google.protobuf.json_format.ParseDict` to coerce
    nested dicts/lists into ``Struct``. No bytes handling is needed because
    OpenAI Responses items are JSON-shaped (binary content is base64'd
    server-side).
    """
    if not payload:
        return
    struct = Struct()
    ParseDict(payload, struct)
    event.extensions[OPENAI_EXTENSION_KEY].CopyFrom(struct)


def _read_openai_extension(event: ProtoMessage) -> dict[str, Any]:
    """Read ``event.extensions['openai']`` back as a plain dict."""
    if not hasattr(event, "extensions") or OPENAI_EXTENSION_KEY not in event.extensions:
        return {}
    return MessageToDict(
        event.extensions[OPENAI_EXTENSION_KEY], preserving_proto_field_name=True
    )


# ----- text + argument coercion ---------------------------------------------


def _extract_message_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"input_text", "output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "".join(texts) or None
    return None


def _parse_arguments(arguments: Any) -> dict[str, Any] | None:
    if arguments is None:
        return None
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"_raw": arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    return {"_value": arguments}


def _serialize_output(output: Any) -> str | None:
    if output is None:
        return None
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output)
    except (TypeError, ValueError):
        return json.dumps(output, default=str)


# ----- cross-framework fallback ---------------------------------------------


def _fallback_reconstruct(event: ProtoMessage) -> dict[str, Any] | None:
    """Best-effort item rebuild when no ``raw_item`` is present.

    Used only for cross-framework reads (e.g. an ADK agent wrote the session
    and we're reading it from OpenAI's side).
    """
    if isinstance(event, UserMessageReceived):
        return {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": event.content if event.HasField("content") else ""}],
        }
    if isinstance(event, AssistantTextGenerated):
        return {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": event.content if event.HasField("content") else ""}],
        }
    if isinstance(event, AssistantToolCallsGenerated):
        if not event.tool_calls:
            return None
        tc = event.tool_calls[0]
        args = MessageToDict(tc.arguments, preserving_proto_field_name=True) if tc.HasField("arguments") else {}
        return {
            "type": "function_call",
            "call_id": tc.call_id,
            "name": tc.tool_name,
            "arguments": json.dumps(args),
        }
    if isinstance(event, ToolResultReceived):
        return {
            "type": "function_call_output",
            "call_id": event.call_id,
            "output": event.result if event.HasField("result") else None,
        }
    return None
```

- [ ] **Step 4: Verify Tasks 1-5 tests pass**

Run: `cd openai-agents/python && pytest tests/test_codec.py -v -k "not session.py"`
Expected: All `TestCanonicalMapping`, `TestNonCanonicalItems`, `TestRoundTrip`, `test_for_session_*`, `test_serialize_*` pass. (Reasoning + MCP tests come in tasks 6 and 7.)

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_codec.py openai-agents/python/tests/test_codec.py
git commit -m "refactor(openai-agents): codec on shared proto canonical events (DEV-1539)"
```

---

### Task 6: Map `reasoning` items to `AssistantThinkingGenerated`

OpenAI Responses items of type `reasoning` come in two shapes per `SCHEMA_v2.md §3.2`:
- **Plaintext** (some Gemini-thinking-style providers piped through): `{"type": "reasoning", "id": "...", "content": [{"type": "reasoning_text", "text": "..."}]}`
- **Encrypted** (OpenAI o-series): `{"type": "reasoning", "id": "...", "encrypted_content": "<opaque blob>", "signature": "<sig>"}`

The opaque blob rides under `extensions.openai.thinking.raw` per the resolved Q4 in `SCHEMA_v2.md §10`.

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/_codec.py`
- Modify: `openai-agents/python/tests/test_codec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codec.py`:

```python
class TestReasoningMapping:
    def test_plaintext_reasoning_maps_to_thinking(self) -> None:
        items = [{
            "type": "reasoning",
            "id": "r1",
            "content": [{"type": "reasoning_text", "text": "because..."}],
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert len(events) == 1
        assert isinstance(events[0], AssistantThinkingGenerated)
        assert events[0].content == "because..."
        # Plaintext path must omit `encrypted` from the wire; under Edition 2024
        # this means *not* setting the field rather than setting it to False.
        assert not events[0].HasField("encrypted")
        assert not events[0].HasField("signature")
        assert _ext(events[0])["raw_item"] == items[0]

    def test_encrypted_reasoning_maps_to_thinking(self) -> None:
        items = [{
            "type": "reasoning",
            "id": "r2",
            "encrypted_content": "AAA-OPAQUE-BLOB-AAA",
            "signature": "sig-deadbeef",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], AssistantThinkingGenerated)
        assert events[0].encrypted is True
        assert events[0].signature == "sig-deadbeef"
        assert not events[0].HasField("content")
        ext = _ext(events[0])
        assert ext["thinking"]["raw"] == "AAA-OPAQUE-BLOB-AAA"
        assert ext["raw_item"] == items[0]

    def test_reasoning_round_trips_through_raw_item(self) -> None:
        items = [{
            "type": "reasoning",
            "id": "r1",
            "content": [{"type": "reasoning_text", "text": "thinking..."}],
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert canonical_to_items(events) == items
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd openai-agents/python && pytest tests/test_codec.py::TestReasoningMapping -v`
Expected: FAIL — `reasoning` currently falls through to `OpenAIItem`.

- [ ] **Step 3: Add the reasoning mapper to `_codec.py`**

In `_codec.py`, extend `_CANONICAL_ITEM_TYPES`:

```python
_CANONICAL_ITEM_TYPES = frozenset({
    "message",
    "function_call",
    "function_call_output",
    "reasoning",
})
```

In `items_to_canonical`, add a branch before the catch-all:

```python
        elif kind == "reasoning":
            results.append(_map_reasoning(item, message_index, ts))
```

Add the mapper at the end of the per-item-type section:

```python
def _map_reasoning(
    item: dict[str, Any], message_index: int, ts: datetime
) -> AssistantThinkingGenerated:
    """Map an OpenAI ``reasoning`` item to ``AssistantThinkingGenerated``.

    Two shapes per SCHEMA_v2 §3.2:
    - Plaintext: ``content[*].text`` carries reasoning text; ``encrypted=False``.
    - Encrypted (o-series): opaque ``encrypted_content`` + optional ``signature``;
      blob rides under ``extensions.openai.thinking.raw``.
    """
    evt = AssistantThinkingGenerated(message_index=message_index)
    evt.timestamp.FromDatetime(ts.astimezone(UTC).replace(tzinfo=None))

    text = _extract_reasoning_text(item.get("content") or item.get("summary"))
    encrypted_blob = item.get("encrypted_content")
    signature = item.get("signature")

    ext_payload: dict[str, Any] = {"raw_item": dict(item), "item_type": "reasoning"}

    if encrypted_blob is not None:
        # Only set the field when True. Edition 2024 emits any explicitly-set
        # field on the wire (`"encrypted": false` would drift from the canonical
        # fixture, which omits the key when reasoning is plaintext).
        evt.encrypted = True
        if signature:
            evt.signature = signature
        ext_payload["thinking"] = {"raw": encrypted_blob}
    else:
        if text is not None:
            evt.content = text
        if signature:
            evt.signature = signature

    _set_openai_extension(evt, ext_payload)
    return evt


def _extract_reasoning_text(blocks: Any) -> str | None:
    """Pull plaintext from a ``reasoning.content`` or ``reasoning.summary`` list.

    Handles ``{"type": "reasoning_text", "text": "..."}`` and
    ``{"type": "summary_text", "text": "..."}`` entries; concatenates text.
    """
    if not isinstance(blocks, list):
        return None
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"reasoning_text", "summary_text", "text"}:
            value = block.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts) or None
```

Extend `_fallback_reconstruct`:

```python
    if isinstance(event, AssistantThinkingGenerated):
        if event.encrypted:
            ext = _read_openai_extension(event)
            blob = ext.get("thinking", {}).get("raw") if isinstance(ext, dict) else None
            item: dict[str, Any] = {"type": "reasoning"}
            if blob is not None:
                item["encrypted_content"] = blob
            if event.HasField("signature"):
                item["signature"] = event.signature
            return item
        text = event.content if event.HasField("content") else ""
        return {
            "type": "reasoning",
            "content": [{"type": "reasoning_text", "text": text}],
        }
```

- [ ] **Step 4: Verify the new tests pass**

Run: `cd openai-agents/python && pytest tests/test_codec.py::TestReasoningMapping tests/test_codec.py::TestRoundTrip -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_codec.py openai-agents/python/tests/test_codec.py
git commit -m "feat(openai-agents): map reasoning items to AssistantThinkingGenerated (DEV-1539)"
```

---

### Task 7: Map MCP approvals to `InterruptIssued` / `InterruptResolved`

OpenAI Responses items:
- `{"type": "mcp_approval_request", "id": "<req>", "name": "<tool>", "arguments": "<json-string>", "server_label": "..."}` → `InterruptIssued(kind="approval", request_id=id, tool_name=name)` + `extensions.openai.interrupt.proposed_call`.
- `{"type": "mcp_approval_response", "approval_request_id": "<req>", "approve": true|false, "reason": "..."}` → `InterruptResolved(request_id=approval_request_id, outcome="allow"|"deny", response=reason)`.

Post-hoc per `SCHEMA_v2.md §3.3`: `request_id == call_id` of the gated tool call (the MCP request id is the same id the eventual tool-call event will carry).

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/_codec.py`
- Modify: `openai-agents/python/tests/test_codec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codec.py`:

```python
class TestMcpApprovalMapping:
    def test_mcp_approval_request_maps_to_interrupt_issued(self) -> None:
        items = [{
            "type": "mcp_approval_request",
            "id": "req-1",
            "name": "publish_post",
            "arguments": '{"title": "hi"}',
            "server_label": "blog-mcp",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], InterruptIssued)
        assert events[0].request_id == "req-1"
        assert events[0].kind == "approval"
        assert events[0].tool_name == "publish_post"
        ext = _ext(events[0])
        assert ext["interrupt"]["proposed_call"] == {
            "id": "req-1",
            "name": "publish_post",
            "arguments": {"title": "hi"},
        }
        assert ext["raw_item"] == items[0]

    def test_mcp_approval_response_allow_maps_to_resolved(self) -> None:
        items = [{
            "type": "mcp_approval_response",
            "approval_request_id": "req-1",
            "approve": True,
            "reason": "looks fine",
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], InterruptResolved)
        assert events[0].request_id == "req-1"
        assert events[0].outcome == "allow"
        assert events[0].response == "looks fine"

    def test_mcp_approval_response_deny_maps_to_resolved(self) -> None:
        items = [{
            "type": "mcp_approval_response",
            "approval_request_id": "req-2",
            "approve": False,
        }]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        assert isinstance(events[0], InterruptResolved)
        assert events[0].outcome == "deny"
        assert not events[0].HasField("response")

    def test_mcp_round_trip(self) -> None:
        items = [
            {
                "type": "mcp_approval_request",
                "id": "req-1",
                "name": "publish_post",
                "arguments": '{"title": "hi"}',
                "server_label": "blog-mcp",
            },
            {
                "type": "mcp_approval_response",
                "approval_request_id": "req-1",
                "approve": True,
            },
        ]
        events = items_to_canonical(items, start_index=0, timestamp=TS)
        restored = canonical_to_items(events)
        assert restored == items
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd openai-agents/python && pytest tests/test_codec.py::TestMcpApprovalMapping -v`
Expected: FAIL — these items currently fall through to `OpenAIItem`.

- [ ] **Step 3: Extend `_codec.py` with MCP mappers**

Update `_CANONICAL_ITEM_TYPES`:

```python
_CANONICAL_ITEM_TYPES = frozenset({
    "message",
    "function_call",
    "function_call_output",
    "reasoning",
    "mcp_approval_request",
    "mcp_approval_response",
})
```

In `items_to_canonical`, add the branches before the catch-all:

```python
        elif kind == "mcp_approval_request":
            results.append(_map_mcp_approval_request(item, message_index, ts))
        elif kind == "mcp_approval_response":
            results.append(_map_mcp_approval_response(item, message_index, ts))
```

Add the mappers:

```python
def _map_mcp_approval_request(
    item: dict[str, Any], message_index: int, ts: datetime
) -> InterruptIssued:
    """Map an MCP ``mcp_approval_request`` item to ``InterruptIssued``.

    Post-hoc gate per SCHEMA_v2 §3.3: ``request_id`` equals the gated tool
    call's ``call_id``. The proposed call rides under
    ``extensions.openai.interrupt.proposed_call`` per the documented soft
    convention.
    """
    request_id = item.get("id") or ""
    name = item.get("name") or ""
    args = _parse_arguments(item.get("arguments"))

    evt = InterruptIssued(
        request_id=request_id,
        kind="approval",
    )
    if name:
        evt.tool_name = name
    evt.timestamp.FromDatetime(ts.astimezone(UTC).replace(tzinfo=None))

    proposed_call: dict[str, Any] = {
        "id": request_id,
        "name": name,
        "arguments": args if args is not None else {},
    }
    _set_openai_extension(evt, {
        "raw_item": dict(item),
        "item_type": "mcp_approval_request",
        "interrupt": {"proposed_call": proposed_call},
    })
    return evt


def _map_mcp_approval_response(
    item: dict[str, Any], message_index: int, ts: datetime
) -> InterruptResolved:
    """Map ``mcp_approval_response`` to ``InterruptResolved``.

    ``approve=True`` ⇒ ``outcome=allow``; ``approve=False`` ⇒ ``outcome=deny``.
    Free-text rationale (when present) goes in canonical ``response``.
    """
    evt = InterruptResolved(
        request_id=item.get("approval_request_id") or "",
        outcome="allow" if item.get("approve") else "deny",
    )
    reason = item.get("reason")
    if isinstance(reason, str) and reason:
        evt.response = reason
    evt.timestamp.FromDatetime(ts.astimezone(UTC).replace(tzinfo=None))
    _set_openai_extension(evt, {
        "raw_item": dict(item),
        "item_type": "mcp_approval_response",
    })
    return evt
```

Extend `_fallback_reconstruct` so cross-framework reads still produce a usable item shape:

```python
    if isinstance(event, InterruptIssued):
        ext = _read_openai_extension(event)
        proposed = (ext.get("interrupt") or {}).get("proposed_call") or {}
        return {
            "type": "mcp_approval_request",
            "id": event.request_id,
            "name": event.tool_name if event.HasField("tool_name") else proposed.get("name", ""),
            "arguments": json.dumps(proposed.get("arguments") or {}),
        }
    if isinstance(event, InterruptResolved):
        item: dict[str, Any] = {
            "type": "mcp_approval_response",
            "approval_request_id": event.request_id,
            "approve": event.outcome == "allow",
        }
        if event.HasField("response"):
            item["reason"] = event.response
        return item
```

- [ ] **Step 4: Verify all codec tests pass**

Run: `cd openai-agents/python && pytest tests/test_codec.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_codec.py openai-agents/python/tests/test_codec.py
git commit -m "feat(openai-agents): map MCP approvals to Interrupt events (DEV-1539)"
```

---

### Task 8: Wire `session.py` to the new schema package

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/session.py`
- Modify: `openai-agents/python/kurrent_openai_agents/__init__.py`

- [ ] **Step 1: Rewrite imports + lifecycle filtering in `session.py`**

Replace the entire file with:

```python
"""``KurrentDBSession`` — drop-in ``Session`` implementation for the OpenAI Agents SDK.

Implements the SDK's ``Session`` protocol (``src/agents/memory/session.py``)
by decomposing each session item into canonical events and writing them to
``AgentSession-{session_id}``. Items that don't map onto canonical
conversation events ride as framework-specific ``OpenAIItem`` events
carrying the raw dict. See ``DESIGN.md`` for the full mapping table.

Async — matches the SDK and ``AsyncKurrentDBClient``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents.memory.session import SessionABC
from kurrent_agent_schema import (
    USAGE_METADATA_KEY,
    SessionContinuedAs,
    SessionEnded,
    SessionStarted,
    SubagentCompleted,
    SubagentStarted,
)
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from ._codec import canonical_to_items, items_to_canonical
from ._stream_names import for_session

if TYPE_CHECKING:  # pragma: no cover
    from agents.items import TResponseInputItem
    from agents.memory.session_settings import SessionSettings


logger = logging.getLogger("kurrent_openai_agents.session")

# Lifecycle / out-of-conversation event types that get_items must skip.
# Subagent lifecycle is canonical in v2 (SCHEMA_v2 §3.5) but lives on the
# parent session stream — same skip treatment as session lifecycle.
_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset({
    "SessionStarted",
    "SessionEnded",
    "SessionContinuedAs",
    "SubagentStarted",
    "SubagentCompleted",
})

_LIFECYCLE_PROTO_TYPES: tuple[type, ...] = (
    SessionStarted,
    SessionEnded,
    SessionContinuedAs,
    SubagentStarted,
    SubagentCompleted,
)


class KurrentDBSession(SessionABC):
    """KurrentDB-backed ``Session`` implementation.

    One stream per session: ``AgentSession-{session_id}``. First write per
    session emits a ``SessionStarted`` lifecycle event carrying ``app_name``
    and ``user_id`` (constructor configuration — the OpenAI Agents SDK
    itself has no native app/user concept).
    """

    def __init__(
        self,
        session_id: str,
        *,
        client: AsyncKurrentDBClient,
        app_name: str | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
        session_settings: SessionSettings | None = None,
    ) -> None:
        self.session_id = session_id
        self.session_settings = session_settings
        self._client = client
        self._app_name = app_name
        self._user_id = user_id
        self._agent_name = agent_name
        self._stream = for_session(session_id)

    # ----- Session protocol --------------------------------------------------

    async def get_items(
        self, limit: int | None = None
    ) -> list[TResponseInputItem]:
        """Retrieve conversation history, newest-last."""
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return []

        canonical_events: list = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue
            if isinstance(event, _LIFECYCLE_PROTO_TYPES):
                continue
            canonical_events.append(event)

        items = canonical_to_items(canonical_events)
        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items  # type: ignore[return-value]

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Append items to the session stream, emitting canonical events."""
        if not items:
            return

        start_index = await self._count_items()

        canonical_events = items_to_canonical(
            [dict(item) for item in items],  # type: ignore[arg-type]
            start_index=start_index,
        )
        if not canonical_events:
            return

        if start_index == 0:
            await self._emit_session_started_if_missing()

        new_events = [
            _serialization.serialize(
                event,
                metadata=self._event_metadata_for(item, event),
            )
            for item, event in zip(items, canonical_events, strict=False)
        ]
        await self._client.append_to_stream(
            self._stream,
            events=new_events,
            current_version=StreamState.ANY,
        )

    async def pop_item(self) -> TResponseInputItem | None:
        """Best-effort pop — returns the last item but does not remove it.

        Append-only storage means proper "pop" needs a tombstone marker;
        deferred until a concrete caller needs it (DESIGN.md §8 Q1).
        """
        items = await self.get_items()
        if not items:
            return None
        logger.warning(
            "pop_item() on KurrentDBSession is best-effort — the item is "
            "returned but remains in the stream."
        )
        return items[-1]

    async def clear_session(self) -> None:
        """Append a ``SessionEnded`` marker. The stream remains for audit."""
        ended = SessionEnded(reason="cleared")
        ended.timestamp.FromDatetime(datetime.now(UTC).replace(tzinfo=None))
        await self._client.append_to_stream(
            self._stream,
            events=[_serialization.serialize(ended)],
            current_version=StreamState.ANY,
        )

    # ----- internals ---------------------------------------------------------

    async def _count_items(self) -> int:
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return 0
        return sum(
            1 for r in recorded if r.type not in _LIFECYCLE_EVENT_TYPES
        )

    async def _emit_session_started_if_missing(self) -> None:
        started = SessionStarted()
        started.timestamp.FromDatetime(datetime.now(UTC).replace(tzinfo=None))
        if self._app_name:
            started.app_name = self._app_name
        if self._user_id:
            started.user_id = self._user_id
        if self._agent_name:
            started.agent_name = self._agent_name
        try:
            await self._client.append_to_stream(
                self._stream,
                events=[_serialization.serialize(started)],
                current_version=StreamState.NO_STREAM,
            )
        except Exception:
            # Stream exists — SessionStarted already written. Benign.
            pass

    def _event_metadata_for(
        self, item: Any, event: Any
    ) -> dict[str, Any] | None:
        """Build the KurrentDB event metadata dict for an OpenAI item.

        v0: no automatic ``$usage`` mapping. The SDK aggregates usage at the
        run level, not per-item. ``USAGE_METADATA_KEY`` is imported here for
        subclassers who want to attach per-item usage from out-of-band data.
        """
        del item, event, USAGE_METADATA_KEY  # imported for subclassers
        return None
```

- [ ] **Step 2: Update `__init__.py` to re-export `OpenAIItem`**

Replace `kurrent_openai_agents/__init__.py` with:

```python
"""Kurrent integration for the OpenAI Agents SDK (Python).

Drop-in ``Session`` implementation backed by KurrentDB, sharing the canonical
event schema with the Google ADK, Microsoft Agent Framework, Strands, and
Claude Agent SDK integrations in this monorepo. See ``DESIGN.md``.
"""

from . import client
from ._openai_events import OPENAI_EXTENSION_KEY, OpenAIItem
from .session import KurrentDBSession

__all__ = [
    "KurrentDBSession",
    "OpenAIItem",
    "OPENAI_EXTENSION_KEY",
    "client",
]
```

- [ ] **Step 3: Run the integration session tests**

Ensure KurrentDB Testcontainer is reachable for `pytest`. Pass `--timeout=60` per the persistent-subscription guidance — same applies to async streams here.

Run: `cd openai-agents/python && pytest tests/test_session.py -v --timeout=60`
Expected: All tests pass — round-trip conversation, incremental appends, limit, clear, reasoning verbatim.

- [ ] **Step 4: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/session.py openai-agents/python/kurrent_openai_agents/__init__.py
git commit -m "refactor(openai-agents): session on shared schema; v2 lifecycle skip-list (DEV-1539)"
```

---

### Task 9: Add session-level coverage for thinking + MCP approvals

**Files:**
- Modify: `openai-agents/python/tests/test_session.py`

- [ ] **Step 1: Append the new test cases**

Append to `tests/test_session.py`:

```python
class TestThinking:
    async def test_plaintext_reasoning_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        original = {
            "type": "reasoning",
            "id": "r1",
            "content": [{"type": "reasoning_text", "text": "thinking..."}],
        }
        await session.add_items([original])
        [restored] = await session.get_items()
        assert restored == original

    async def test_encrypted_reasoning_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        original = {
            "type": "reasoning",
            "id": "r2",
            "encrypted_content": "AAA-OPAQUE-AAA",
            "signature": "sig-deadbeef",
        }
        await session.add_items([original])
        [restored] = await session.get_items()
        assert restored == original


class TestMcpApprovals:
    async def test_mcp_approval_pair_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        items = [
            {
                "type": "mcp_approval_request",
                "id": "req-1",
                "name": "publish_post",
                "arguments": '{"title": "hi"}',
                "server_label": "blog-mcp",
            },
            {
                "type": "mcp_approval_response",
                "approval_request_id": "req-1",
                "approve": True,
                "reason": "looks fine",
            },
        ]
        await session.add_items(items)
        restored = await session.get_items()
        assert restored == items
```

- [ ] **Step 2: Run the new tests**

Run: `cd openai-agents/python && pytest tests/test_session.py::TestThinking tests/test_session.py::TestMcpApprovals -v --timeout=60`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add openai-agents/python/tests/test_session.py
git commit -m "test(openai-agents): session-level thinking + MCP approval round-trip (DEV-1539)"
```

---

### Task 10: Delete the now-unused `_schema/` subpackage

**Files:**
- Delete: `openai-agents/python/kurrent_openai_agents/_schema/__init__.py`
- Delete: `openai-agents/python/kurrent_openai_agents/_schema/events.py`
- Delete: `openai-agents/python/kurrent_openai_agents/_schema/stream_names.py`

- [ ] **Step 1: Confirm no remaining imports**

Run: `cd openai-agents/python && grep -rn "_schema" kurrent_openai_agents tests`
Expected: no matches.

- [ ] **Step 2: Delete the subpackage**

```bash
rm -r openai-agents/python/kurrent_openai_agents/_schema
```

- [ ] **Step 3: Run the full test suite**

Run: `cd openai-agents/python && pytest -v --timeout=60`
Expected: All tests pass.

- [ ] **Step 4: Lint**

Run: `cd openai-agents/python && ruff check kurrent_openai_agents tests`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add -A openai-agents/python/kurrent_openai_agents/_schema
git commit -m "chore(openai-agents): drop vendored _schema/ subpackage (DEV-1539)"
```

---

## Phase 2 — Fixture Round-Trip Tests (DEV-1540)

### Task 11: Add fixture-driven drift detection

**Files:**
- Create: `openai-agents/python/tests/test_fixtures_round_trip.py`

- [ ] **Step 1: Write the test module**

Create `openai-agents/python/tests/test_fixtures_round_trip.py`:

```python
"""Drift-detection tests for the OpenAI Agents Python write + read path.

For every canonical fixture under ``schema/fixtures/events/``:

1. Parse into the canonical record via ``EVENT_TYPE_BY_NAME``.
2. Pass through :func:`serialization.serialize` (write path).
3. Append to KurrentDB.
4. Read back, deserialise, and re-serialise the round-tripped record.
5. Assert structural equality with the original fixture.

Pairs with the schema package's per-fixture round-trip test and with the
matching MAF Python / Strands integrations' tests, so a future schema
change cannot silently bypass the OpenAI Agents codec.

Also exercises a bespoke lossless-round-trip for the
``extensions.openai.raw_item`` contract: ingest an OpenAI Agents item →
decompose to canonical → re-compose → compare to the original dict.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from google.protobuf.message import Message
from kurrent_agent_schema import EVENT_TYPE_BY_NAME, from_json
from kurrentdbclient import AsyncKurrentDBClient, StreamState

from kurrent_openai_agents import _serialization
from kurrent_openai_agents._codec import canonical_to_items, items_to_canonical


def _locate_fixtures_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "schema" / "fixtures"
        if (candidate / "events").is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate schema/fixtures relative to this test tree."
    )


FIXTURES_ROOT = _locate_fixtures_root()
EVENTS_DIR = FIXTURES_ROOT / "events"


def _normalise(obj):
    if isinstance(obj, dict):
        return {k: _normalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_normalise(x) for x in obj]
    return obj


def _fixture_cases() -> list[Path]:
    return sorted(EVENTS_DIR.glob("*.json"))


def _parse(fixture_path: Path) -> tuple[dict, Message]:
    raw = fixture_path.read_text(encoding="utf-8")
    original = json.loads(raw)
    model = EVENT_TYPE_BY_NAME.get(fixture_path.stem)
    assert model is not None, f"No canonical model registered for '{fixture_path.stem}'"
    parsed = from_json(model, raw)
    return original, parsed


def test_every_canonical_event_has_a_fixture() -> None:
    missing = [
        name for name in EVENT_TYPE_BY_NAME
        if not (EVENTS_DIR / f"{name}.json").exists()
    ]
    assert not missing, f"Missing fixtures for canonical events: {missing}"


@pytest.mark.parametrize("fixture_path", _fixture_cases(), ids=lambda p: p.stem)
def test_fixture_codec_round_trip(fixture_path: Path) -> None:
    """Pure-codec round-trip — no KurrentDB."""
    original, parsed = _parse(fixture_path)

    new_event = _serialization.serialize(parsed)
    written = json.loads(new_event.data)
    assert _normalise(written) == _normalise(original), (
        f"Drift on serialise for {fixture_path.stem}: "
        f"{json.dumps(_normalise(written))} != {json.dumps(_normalise(original))}"
    )


@pytest.mark.parametrize("fixture_path", _fixture_cases(), ids=lambda p: p.stem)
async def test_fixture_kurrentdb_round_trip(
    fixture_path: Path, kurrentdb_client: AsyncKurrentDBClient
) -> None:
    """Server-side round-trip — write fixture, read it back, re-serialise, compare."""
    original, parsed = _parse(fixture_path)

    new_event = _serialization.serialize(parsed)
    stream = f"FixtureRoundTrip-{fixture_path.stem}-{uuid.uuid4().hex[:8]}"
    await kurrentdb_client.append_to_stream(
        stream, events=[new_event], current_version=StreamState.NO_STREAM,
    )

    [recorded] = await kurrentdb_client.get_stream(stream)
    restored = _serialization.deserialize(recorded)
    assert restored is not None

    re_serialised = json.loads(_serialization.serialize(restored).data)
    assert _normalise(re_serialised) == _normalise(original), (
        f"Drift on KurrentDB round-trip for {fixture_path.stem}"
    )


# ----- raw_item lossless contract -------------------------------------------


_RAW_ITEM_FIXTURES: list[dict] = [
    {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hello"}],
    },
    {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hi"}],
    },
    {
        "type": "function_call",
        "call_id": "c1",
        "name": "search",
        "arguments": '{"q": "x"}',
    },
    {
        "type": "function_call_output",
        "call_id": "c1",
        "output": '{"hits": 3}',
    },
    {
        "type": "reasoning",
        "id": "r1",
        "content": [{"type": "reasoning_text", "text": "because..."}],
    },
    {
        "type": "reasoning",
        "id": "r2",
        "encrypted_content": "AAA-OPAQUE-AAA",
        "signature": "sig",
    },
    {
        "type": "mcp_approval_request",
        "id": "req-1",
        "name": "publish_post",
        "arguments": '{"title": "hi"}',
        "server_label": "blog-mcp",
    },
    {
        "type": "mcp_approval_response",
        "approval_request_id": "req-1",
        "approve": True,
    },
    {
        "type": "handoff_call",
        "call_id": "h1",
        "name": "handoff_to_expert",
        "arguments": "{}",
    },
    {
        "type": "computer_call",
        "call_id": "comp1",
        "action": "screenshot",
    },
]


@pytest.mark.parametrize("item", _RAW_ITEM_FIXTURES, ids=lambda it: it["type"])
def test_raw_item_round_trip(item: dict) -> None:
    """Decompose → canonical event(s) → re-compose → original dict."""
    events = items_to_canonical([item], start_index=0, timestamp=datetime.now(UTC))
    restored = canonical_to_items(events)
    assert restored == [item]
```

- [ ] **Step 2: Run the fixture tests**

Run: `cd openai-agents/python && pytest tests/test_fixtures_round_trip.py -v --timeout=60`
Expected: All fixtures parse, all round-trips succeed.

If `test_fixture_codec_round_trip[SessionStarted]` (or similar) reports drift on `extensions.openai.raw_item` because of the protobuf-Struct integer-as-double quirk: that's a fixture-side concern and the schema package already accounts for it via `_normalise`. If drift remains on this codec test, raise the issue in the schema repo — do not silence the test here.

- [ ] **Step 3: Commit**

```bash
git add openai-agents/python/tests/test_fixtures_round_trip.py
git commit -m "test(openai-agents): fixture round-trip + raw_item lossless contract (DEV-1540)"
```

---

## Phase 3 — Documentation (DEV-1541)

### Task 12: Refresh `DESIGN.md`

**Files:**
- Modify: `openai-agents/python/DESIGN.md`

- [ ] **Step 1: Update §3 mapping table**

In `openai-agents/python/DESIGN.md`, replace the "Write path (`add_items`)" mapping table with the v2 table:

```markdown
### Write path (`add_items`)

| OpenAI item `type` | Canonical event(s) | Notes |
|---|---|---|
| `message` (role=user) | `UserMessageReceived` | |
| `message` (role=assistant) | `AssistantTextGenerated` | |
| `function_call` | `AssistantToolCallsGenerated` (one tool_call per event) | |
| `function_call_output` | `ToolResultReceived` | |
| `reasoning` | `AssistantThinkingGenerated` | Plaintext content, or `encrypted=true` + `signature` for o-series; opaque blob in `extensions.openai.thinking.raw`. SCHEMA_v2 §3.2. |
| `mcp_approval_request` | `InterruptIssued` (`kind="approval"`) | Proposed call under `extensions.openai.interrupt.proposed_call`; post-hoc gate (`request_id == call_id`). SCHEMA_v2 §3.3. |
| `mcp_approval_response` | `InterruptResolved` | `outcome=allow|deny` from `approve`; `response` from `reason`. |
| `handoff_call` / `handoff_output` | `OpenAIItem` | Deferred — see DEV-1684 for the canonical `SubagentStarted` / `SubagentCompleted` promotion. |
| `computer_call`, `shell_call`, `web_search`, … | `OpenAIItem` | No canonical analogue. |

**Every emitted event also stashes the full original item under `extensions.openai.raw_item`** — lossless reconstruction regardless of which branch it took.
```

- [ ] **Step 2: Add a "Schema dependency" section**

After §3, before §4, insert:

```markdown
## 3.5 Schema dependency

The integration depends on the shared `kurrent-agent-schema` Python package
(see [`../../schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md)). Canonical
event types are protobuf messages (Edition 2024); the integration imports
them directly from `kurrent_agent_schema`. Stream-name builders and the
`$usage` metadata key come from the same package.

The framework-specific `OpenAIItem` event remains a local Pydantic model in
`_openai_events.py` because it is not part of the cross-framework contract.

JSON wire format goes through the schema package's `to_json` / `from_json`
helpers exclusively — direct calls to `google.protobuf.json_format` are not
supported on this code path. Each event's metadata carries
`$schema_version = 2` per SCHEMA_v2 §9.
```

- [ ] **Step 3: Refresh §4 OpenAI-specific concepts**

Update §4's bullets so reasoning and MCP approvals reflect their new canonical mapping:

```markdown
## 4. OpenAI-specific concepts

- **Handoffs** — `handoff_call` and `handoff_output` items. LLM-driven nested agent invocation. Currently ride as `OpenAIItem`; promotion to canonical `SubagentStarted` / `SubagentCompleted` (with separate `AgentSubsession-` streams) is tracked under DEV-1684.
- **Guardrails** — runtime checks, not session items; nothing for us to persist.
- **MCP approvals** — `mcp_approval_request` / `mcp_approval_response` decompose into canonical `InterruptIssued` / `InterruptResolved` (`kind="approval"`). Proposed call rides under `extensions.openai.interrupt.proposed_call`; post-hoc gate (request_id == call_id) per SCHEMA_v2 §3.3.
- **Computer / shell tools** — `computer_call`, `shell_call`. First-class tool types specific to OpenAI's sandbox extensions; ride as `OpenAIItem`.
- **Reasoning** — `reasoning` items decompose into canonical `AssistantThinkingGenerated` per SCHEMA_v2 §3.2. Plaintext content rides on the canonical event; o-series encrypted blobs ride under `extensions.openai.thinking.raw` with `encrypted=true` and `signature` populated on the canonical event.
- **Structured output** — `AgentOutputSchema` / parsed Pydantic payloads. Not a session item; carried on `RunResult` alongside session history.
```

- [ ] **Step 4: Refresh §8 open questions**

Update Q3 (handoffs):

```markdown
3. **Handoff visibility** — handled in DEV-1684. Promotion to canonical `SubagentStarted` / `SubagentCompleted` requires routing the handoff target's items to a separate `AgentSubsession-{parent}-{agent_id}` stream and re-flattening on `get_items`. Out of scope for the schema-v2 cutover.
```

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/DESIGN.md
git commit -m "docs(openai-agents): DESIGN.md for canonical schema v2 (DEV-1541)"
```

---

### Task 13: Refresh README and the monorepo CLAUDE.md row

**Files:**
- Modify: `openai-agents/python/README.md`
- Modify: `CLAUDE.md` (repo root)

- [ ] **Step 1: Update `openai-agents/python/README.md`**

If the README references `SCHEMA.md` (v1), redirect to `SCHEMA_v2.md` and the shared schema package. Add (or replace the relevant lines) at the top of the package overview:

```markdown
Reads and writes the canonical schema defined in [`../../schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md), via the shared [`kurrent-agent-schema`](../../schema/python) Python package. OpenAI-specific items (reasoning, MCP approvals) decompose into the canonical event vocabulary with the original Responses API dict preserved verbatim under `extensions.openai.raw_item`; non-canonical items (handoffs, computer/shell calls) ride as the framework-specific `OpenAIItem` event.
```

- [ ] **Step 2: Update the CLAUDE.md row**

In the "Per-integration design docs" table at the repo root `CLAUDE.md`, replace the OpenAI Agents row with:

```markdown
| OpenAI Agents (Python) | [`openai-agents/python/DESIGN.md`](./openai-agents/python/DESIGN.md) | typed canonical events via shared `kurrent-agent-schema` (Python); decompose items to canonical events (incl. `reasoning` → `AssistantThinkingGenerated`, `mcp_approval_*` → `InterruptIssued`/`InterruptResolved`); full original dict in `extensions.openai.raw_item` for lossless round-trip; non-canonical items wrap as `OpenAIItem`. |
```

- [ ] **Step 3: Commit**

```bash
git add openai-agents/python/README.md CLAUDE.md
git commit -m "docs(openai-agents): README + monorepo row reflect schema v2 (DEV-1541)"
```

---

## Final Verification

- [ ] **Step 1: Run the entire integration test suite**

Run: `cd openai-agents/python && pytest -v --timeout=60`
Expected: All pass.

- [ ] **Step 2: Run lint**

Run: `cd openai-agents/python && ruff check kurrent_openai_agents tests`
Expected: clean.

- [ ] **Step 3: Confirm no leftover references to the removed module**

Run: `grep -rn "_schema" openai-agents/python/`
Expected: no matches.

- [ ] **Step 4: Confirm `$schema_version` is on the wire**

Sanity-check by running a session test and inspecting the metadata of one written event — but the `test_serialize_stamps_schema_version` unit test from Task 4 already pins this. No new step needed.
