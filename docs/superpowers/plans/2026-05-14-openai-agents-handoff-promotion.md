# OpenAI Agents — Handoff Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote OpenAI Agents SDK handoffs to canonical `SubagentStarted` / `SubagentCompleted` events on schema v2, with atomic dual-stream emission, lossless flat-replay on `get_items`, and graceful degradation when run hooks aren't wired.

**Architecture:** `KurrentDBSession` becomes a dual implementation of `SessionABC` + `RunHooksBase` (used as `Runner.run(..., session=s, hooks=s)`). A per-session `_HandoffLedger` populated by `on_handoff` lifecycle hooks is consulted by `add_items` to route subagent items to `AgentSubsession-{parent}-{agent_id}` streams. `SubagentStarted` / `SubagentCompleted` are written atomically to BOTH parent and subsession streams via `multi_append_to_stream`, matching Capacitor's projection expectations. New `SCHEMA_v2 §2.4` solidifies cross-framework stream-name identifier conventions.

**Tech Stack:** Python 3.11, `agents>=0.2.0` (OpenAI Agents SDK), `kurrent-agent-schema>=0.3.0`, `kurrentdbclient>=1.2.0`, pytest with asyncio-auto + KurrentDB Testcontainer fixtures.

**Spec:** `docs/superpowers/specs/2026-05-13-openai-agents-handoff-promotion-design.md`

**Linear:** [AI-471](https://linear.app/kurrent/issue/AI-471)

**Test invocation note:** This package's venv ships pytest at `.venv/bin/pytest`. The repo's recurring guidance says to invoke via `uv run pytest`. All commands below use `uv run pytest` from `openai-agents/python/`.

---

## File map

**New:**
- `openai-agents/python/kurrent_openai_agents/_handoffs.py` — pure logic: slug, `derive_agent_id`, `ExpectedHandoff`, `ActiveHandoff`, `_HandoffLedger`, `route_items` splitter.
- `openai-agents/python/tests/test_handoffs.py` — unit tests for the above.
- `openai-agents/python/tests/test_handoff_session.py` — integration tests via real `Runner.run` against KurrentDB Testcontainer.
- `openai-agents/python/tests/fixtures/__init__.py` — empty marker.
- `openai-agents/python/tests/fixtures/subagent_started_openai.json` — fixture exercising `extensions.openai.raw_item` + `extensions.openai.handoff.source_agent`.
- `openai-agents/python/tests/fixtures/subagent_completed_openai.json` — same shape for completion.
- `openai-agents/python/samples/handoff_demo/__init__.py` — empty marker.
- `openai-agents/python/samples/handoff_demo/main.py` — triage-and-specialist sample.
- `openai-agents/python/samples/handoff_demo/README.md` — sample doc.

**Modified:**
- `openai-agents/python/kurrent_openai_agents/_stream_names.py` — add `for_subsession`.
- `openai-agents/python/kurrent_openai_agents/_codec.py` — add `_map_handoff_call` / `_map_handoff_output`; extend `items_to_canonical` to accept optional ledger context.
- `openai-agents/python/kurrent_openai_agents/_serialization.py` — add `serialize_for_multi_append`.
- `openai-agents/python/kurrent_openai_agents/session.py` — make `KurrentDBSession` inherit `RunHooksBase`; add ledger field; implement `on_handoff` / `on_agent_start` / `on_agent_end`; rewrite `add_items` to route via `_handoffs.route_items` and `multi_append_to_stream`; rewrite `get_items` to re-flatten subsession streams.
- `openai-agents/python/kurrent_openai_agents/__init__.py` — no API export change (KurrentDBSession is already exported; its new mixin is transparent to importers).
- `openai-agents/python/tests/test_codec.py` — add cases for the two new fixtures.
- `openai-agents/python/DESIGN.md` — update §4 (handoff catalogue) and §8 Q3 (resolution note).
- `openai-agents/python/README.md` — one paragraph on canonical handoffs.
- `schema/SCHEMA_v2.md` — add §2.4 (identifier conventions); update §3.5 (atomic dual-stream write; agent_id / agent_type field notes).
- `CLAUDE.md` (repo root) — refresh the OpenAI Agents row to mention canonical subagent promotion.

---

## Task 1: Schema doc — add §2.4 identifier conventions and update §3.5

**Files:**
- Modify: `schema/SCHEMA_v2.md` (insert §2.4 after §2.3; edit §3.5 paragraphs)

- [ ] **Step 1: Insert new §2.4 subsection**

Open `schema/SCHEMA_v2.md`. After the existing §2.3 block (Hosted-agent runtime streams), before the `---` separator that introduces §3, insert:

```markdown
### 2.4 Identifier conventions for stream names

All variable-substitution components of stream names (`session_id`, `parent_session_id`, `agent_id`, `app_name`, `user_id`, `scope`, `filename`, `run_id`) MUST conform to the rules below. The shared `kurrent_agent_schema` / `Kurrent.Agent.Schema` packages provide builders that enforce these rules; producers SHOULD call those builders rather than concatenating strings.

**Character set.** ASCII `[A-Za-z0-9._-]+`, max 128 bytes per component. Producers MUST reject or URL-encode anything outside that set.

**GUID-shaped values.** When a component value parses as a UUID/GUID, producers MUST emit it in **lowercase, dashless** form (the .NET `"N"` format, e.g. `8d77fd28fda0485f9ae18ee9c7fc3751`). Readers MUST also accept the hyphenated `"D"` form as a legacy-compat fallback (matches Capacitor's `SessionStreamCandidates`). Lowercase only — case-sensitivity differences between writers would split a single conversation across two streams.

**Non-GUID values.** Used verbatim after the character-set check. Case-preserved.

**Compound suffix separators.** Where a stream name has two components joined by `-` (e.g. `AgentSubsession-{parent}-{agent_id}`, `AgentMemory-{app}-{user}`), the separator is a single `-`. Neither component may begin or end with `-`. Inner `-` characters within a component are permitted (so `agent_id = "sub-research-x9k2"` is valid; consumers parse right-to-left from the prefix to locate the component boundary).
```

- [ ] **Step 2: Update §3.5 — atomic dual-stream write paragraph**

In §3.5, replace the line that reads:

```
**`SubagentStarted`** (written to parent `AgentSession-` stream)
```

with:

```
**`SubagentStarted`** (written **atomically to BOTH** the parent `AgentSession-` stream and the `AgentSubsession-` stream via `multi_append`)
```

Apply the analogous edit to the `SubagentCompleted` heading line.

After the existing `SubagentStarted` field table, add this paragraph before the `SubagentCompleted` heading:

```
The dual-stream write lets a reader landing on the subsession stream learn its lifecycle without joining back to the parent (Capacitor's trace-tree projector and per-agent eval queries depend on this). The two appends MUST happen in a single `multi_append` call — partial states (parent marker without subsession marker) are not a supported reader state.
```

- [ ] **Step 3: Update §3.5 — field notes for `agent_id` and `agent_type`**

In §3.5's `SubagentStarted` field table, the rows for `agent_id` and `agent_type` change their right-most column:

```
| `agent_id` | string | yes | Opaque, producer-chosen, unique-per-invocation. Recommended shape: `{role_slug}-{short_unique}`. Must satisfy §2.4 character set. |
| `agent_type` | string? | no | Producer-defined role/category string; opaque to canonical readers. Examples: `research`, `code-reviewer`, `general-purpose`, `TriageAgent`. |
```

- [ ] **Step 4: Run schema-doc lint / tests**

Run: `cd schema && uv run pytest -q 2>&1 | tail -20` (the schema python tests, which include doc-coverage assertions).
Expected: PASS (the doc edits don't change any code paths the tests exercise; serves as a smoke check that nothing else broke).

- [ ] **Step 5: Commit**

```bash
git add schema/SCHEMA_v2.md
git commit -m "$(cat <<'EOF'
docs(schema): solidify stream-name identifier conventions (§2.4) and dual-stream subagent lifecycle (§3.5)

Locks the cross-framework conventions for stream-name id components
(safe-chars, GUID→dashless lowercase, compound separator rules) and
mandates atomic dual-stream emission of SubagentStarted/Completed so
Capacitor's trace projector reads subsessions self-describingly. Sets
the contract that AI-471's OpenAI Agents implementation and AI-621's
MAF .NET conformance fix consume.
EOF
)"
```

---

## Task 2: Add `for_subsession` stream-name helper

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/_stream_names.py`
- Test: `openai-agents/python/tests/test_handoffs.py` (created here)

- [ ] **Step 1: Write the failing tests**

Create `openai-agents/python/tests/test_handoffs.py` with:

```python
"""Unit tests for the pure-logic ``_handoffs`` module + stream-name helpers."""

from __future__ import annotations

import pytest

from kurrent_openai_agents._stream_names import for_session, for_subsession


def test_for_subsession_uses_canonical_builder() -> None:
    assert for_subsession("sess-1", "sub-x-abc123") == "AgentSubsession-sess-1-sub-x-abc123"


def test_for_subsession_rejects_empty_parent() -> None:
    with pytest.raises(ValueError, match="parent_session_id"):
        for_subsession("", "sub-x-abc")


def test_for_subsession_rejects_empty_agent_id() -> None:
    with pytest.raises(ValueError, match="agent_id"):
        for_subsession("sess-1", "")


def test_for_subsession_normalises_unsafe_chars() -> None:
    # spaces are not in the safe-char set per §2.4 — must be URL-encoded.
    assert for_subsession("sess 1", "sub x") == "AgentSubsession-sess%201-sub%20x"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: FAIL with `ImportError: cannot import name 'for_subsession'`.

- [ ] **Step 3: Add the `for_subsession` helper**

In `openai-agents/python/kurrent_openai_agents/_stream_names.py`, replace the import line and add a new function. The full updated file should be:

```python
"""Stream-name builders for the OpenAI Agents integration.

Wraps :func:`kurrent_agent_schema.agent_session_stream` and
:func:`kurrent_agent_schema.agent_subsession_stream` with id normalisation
so free-form ids cannot break the category prefix. Conformant with
``schema/SCHEMA_v2.md §2.4``.
"""

from __future__ import annotations

import urllib.parse

from kurrent_agent_schema import agent_session_stream, agent_subsession_stream

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


def for_subsession(parent_session_id: str, agent_id: str) -> str:
    """Subagent conversation stream — ``AgentSubsession-{parent}-{agent_id}``.

    See ``schema/SCHEMA_v2.md §3.5`` for the canonical shape and §2.4 for
    the identifier rules enforced by the underlying normaliser.
    """
    return agent_subsession_stream(
        _normalise_id(parent_session_id, field="parent_session_id"),
        _normalise_id(agent_id, field="agent_id"),
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_stream_names.py openai-agents/python/tests/test_handoffs.py
git commit -m "$(cat <<'EOF'
feat(openai-agents): add for_subsession stream-name helper

Wraps kurrent_agent_schema.agent_subsession_stream with the same id
normalisation as for_session, enforcing the SCHEMA_v2 §2.4 character set
and length budget on parent_session_id and agent_id components.
EOF
)"
```

---

## Task 3: `_handoffs.py` — slug, derive_agent_id, dataclasses, route_items

**Files:**
- Create: `openai-agents/python/kurrent_openai_agents/_handoffs.py`
- Test: `openai-agents/python/tests/test_handoffs.py` (extend)

- [ ] **Step 1: Write failing tests for `slug` and `derive_agent_id`**

Append to `openai-agents/python/tests/test_handoffs.py`:

```python
from kurrent_openai_agents._handoffs import (
    ActiveHandoff,
    ExpectedHandoff,
    _HandoffLedger,
    derive_agent_id,
    slug,
)


# ----- slug --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ResearchAgent", "researchagent"),
    ("Code Reviewer", "code-reviewer"),
    ("multi   word", "multi-word"),
    ("-leading-and-trailing-", "leading-and-trailing"),
    ("UPPER_under_score", "upper_under_score"),
    ("with!punct?.", "with-punct"),
    ("", ""),
])
def test_slug_normalises(raw: str, expected: str) -> None:
    assert slug(raw) == expected


# ----- derive_agent_id ---------------------------------------------------

def test_derive_agent_id_format() -> None:
    aid = derive_agent_id("ResearchAgent", "call_abc123def456")
    assert aid == "sub-researchagent-def456"


def test_derive_agent_id_unique_per_call_id() -> None:
    a = derive_agent_id("Spec", "call_111111")
    b = derive_agent_id("Spec", "call_222222")
    assert a != b
    assert a.startswith("sub-spec-") and b.startswith("sub-spec-")


def test_derive_agent_id_handles_short_call_id() -> None:
    # call_id shorter than the 6-char tail window still produces a valid id.
    aid = derive_agent_id("X", "abc")
    assert aid == "sub-x-abc"


def test_derive_agent_id_rejects_empty_target_name() -> None:
    with pytest.raises(ValueError, match="target_name"):
        derive_agent_id("", "call_1")


def test_derive_agent_id_rejects_empty_call_id() -> None:
    with pytest.raises(ValueError, match="call_id"):
        derive_agent_id("X", "")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: FAIL with `ImportError: cannot import name 'derive_agent_id' from 'kurrent_openai_agents._handoffs'`.

- [ ] **Step 3: Create the `_handoffs.py` skeleton**

Create `openai-agents/python/kurrent_openai_agents/_handoffs.py`:

```python
"""Per-session handoff state + pure routing logic.

Hosts the data structures populated by ``KurrentDBSession`` 's RunHooksBase
overrides (``on_handoff``, ``on_agent_start``, ``on_agent_end``) and consumed
by ``add_items`` to split a flat OpenAI Agents item list into per-stream
segments matching SCHEMA_v2 §3.5 subagent lifecycle.

No I/O here. ``session.py`` owns all KurrentDB interactions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from agents import Agent


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_AGENT_ID_TAIL_LEN = 6


def slug(value: str) -> str:
    """Lowercase + replace any run of non-`[a-z0-9]` with a single `-`; strip ends."""
    return _SLUG_NON_ALNUM.sub("-", value.lower()).strip("-")


def derive_agent_id(target_name: str, call_id: str) -> str:
    """Build a SCHEMA_v2 §2.4-conformant ``agent_id`` for an OpenAI handoff.

    Shape: ``sub-{slug(target_name)}-{call_id[-6:]}``. Unique per handoff
    invocation in a session because ``call_id`` is unique per SDK tool call.
    """
    if not target_name:
        raise ValueError("target_name cannot be empty")
    if not call_id:
        raise ValueError("call_id cannot be empty")
    name_slug = slug(target_name) or "agent"
    tail = call_id[-_AGENT_ID_TAIL_LEN:].lower()
    # Final §2.4 char-set sweep on the tail — call_ids from OpenAI are ascii
    # alphanumeric but a defensive normaliser keeps producers honest.
    tail = _SLUG_NON_ALNUM.sub("-", tail).strip("-") or "x"
    return f"sub-{name_slug}-{tail}"


# ----- ledger data ----------------------------------------------------------


@dataclass(slots=True)
class ExpectedHandoff:
    """Stashed by ``on_handoff`` until the next matching ``function_call`` arrives."""

    from_name: str
    to_name: str
    to_agent: Any  # agents.Agent — kept as Any to avoid an import cycle


@dataclass(slots=True)
class ActiveHandoff:
    """Tracks an in-flight subagent invocation until its ``handoff_output`` lands."""

    call_id: str
    agent_id: str
    agent_type: str
    subsession_stream: str


@dataclass(slots=True)
class _HandoffLedger:
    """Per-session mutable state. Lives on a ``KurrentDBSession`` instance.

    Not persisted; rebuilt on process restart via fresh ``on_handoff`` events.
    See spec §5 "Session resume after process restart" for the rationale.
    """

    parent_stream: str
    expected: ExpectedHandoff | None = None
    active: dict[str, ActiveHandoff] = field(default_factory=dict)
    current_owner: str = ""  # set in __post_init__

    def __post_init__(self) -> None:
        if not self.current_owner:
            self.current_owner = self.parent_stream
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: PASS (all slug / derive_agent_id tests).

- [ ] **Step 5: Write failing tests for `route_items`**

Append to `tests/test_handoffs.py`:

```python
from datetime import UTC, datetime

from kurrent_agent_schema import (
    AssistantTextGenerated,
    SubagentCompleted,
    SubagentStarted,
    UserMessageReceived,
)

from kurrent_openai_agents._handoffs import DualAppend, SingleAppend, route_items


def _make_ledger(parent: str = "AgentSession-test") -> _HandoffLedger:
    return _HandoffLedger(parent_stream=parent)


def _ts() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)


def test_route_items_all_parent_when_no_handoff() -> None:
    ledger = _make_ledger()
    items = [{"type": "message", "role": "user", "content": "hello"}]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )
    assert len(ops) == 1
    assert isinstance(ops[0], SingleAppend)
    assert ops[0].stream == "AgentSession-test"
    assert isinstance(ops[0].events[0], UserMessageReceived)


def test_route_items_emits_subagent_started_when_expected_set() -> None:
    ledger = _make_ledger("AgentSession-sess-1")

    class _FakeAgent:
        name = "ResearchAgent"

    ledger.expected = ExpectedHandoff(
        from_name="TriageAgent", to_name="ResearchAgent", to_agent=_FakeAgent()
    )
    items = [
        {
            "type": "function_call",
            "call_id": "call_xyz123def456",
            "name": "transfer_to_researchagent",
            "arguments": '{"topic": "ACME"}',
        },
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    # First op is the atomic dual-stream SubagentStarted write.
    assert len(ops) == 1
    assert isinstance(ops[0], DualAppend)
    assert ops[0].streams[0] == "AgentSession-sess-1"
    assert ops[0].streams[1].startswith("AgentSubsession-sess-1-sub-researchagent-")
    assert isinstance(ops[0].event, SubagentStarted)
    assert ops[0].event.agent_type == "ResearchAgent"

    # Ledger now active and current_owner flipped to subsession.
    assert "call_xyz123def456" in ledger.active
    assert ledger.current_owner.startswith("AgentSubsession-sess-1-sub-researchagent-")
    assert ledger.expected is None


def test_route_items_emits_subagent_completed_on_matching_output() -> None:
    ledger = _make_ledger("AgentSession-sess-1")
    subsession = "AgentSubsession-sess-1-sub-researchagent-def456"
    ledger.active["call_xyz123def456"] = ActiveHandoff(
        call_id="call_xyz123def456",
        agent_id="sub-researchagent-def456",
        agent_type="ResearchAgent",
        subsession_stream=subsession,
    )
    ledger.current_owner = subsession

    items = [
        {
            "type": "function_call_output",
            "call_id": "call_xyz123def456",
            "output": "ACME Q1 summary: ...",
        },
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    assert len(ops) == 1
    assert isinstance(ops[0], DualAppend)
    assert ops[0].streams == ("AgentSession-sess-1", subsession)
    assert isinstance(ops[0].event, SubagentCompleted)
    assert ops[0].event.agent_id == "sub-researchagent-def456"

    # Ledger cleared and current_owner flipped back to parent.
    assert "call_xyz123def456" not in ledger.active
    assert ledger.current_owner == "AgentSession-sess-1"


def test_route_items_routes_inner_subagent_items_to_subsession() -> None:
    ledger = _make_ledger("AgentSession-sess-1")
    subsession = "AgentSubsession-sess-1-sub-researchagent-def456"
    ledger.active["call_xyz"] = ActiveHandoff(
        call_id="call_xyz",
        agent_id="sub-researchagent-def456",
        agent_type="ResearchAgent",
        subsession_stream=subsession,
    )
    ledger.current_owner = subsession

    items = [
        {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "researched"}],
        },
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=5, timestamp=_ts()
    )

    assert len(ops) == 1
    assert isinstance(ops[0], SingleAppend)
    assert ops[0].stream == subsession
    assert isinstance(ops[0].events[0], AssistantTextGenerated)


def test_route_items_handoff_call_without_expected_falls_through() -> None:
    """Hooks-not-wired degradation: looks like a regular function call → OpenAIItem."""
    ledger = _make_ledger("AgentSession-sess-1")
    items = [{
        "type": "function_call",
        "call_id": "call_z",
        "name": "transfer_to_x",
        "arguments": "{}",
    }]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    # All routed to parent as single appends; no DualAppend, no SubagentStarted.
    assert all(isinstance(op, SingleAppend) and op.stream == "AgentSession-sess-1" for op in ops)
    flat_events = [e for op in ops for e in op.events]
    assert not any(isinstance(e, SubagentStarted) for e in flat_events)


def test_route_items_preserves_inter_op_ordering() -> None:
    """Ordering invariant: handoff body items appear in ops AFTER the SubagentStarted DualAppend."""
    ledger = _make_ledger("AgentSession-sess-1")

    class _FakeAgent: name = "X"

    ledger.expected = ExpectedHandoff(from_name="T", to_name="X", to_agent=_FakeAgent())

    items = [
        {"type": "function_call", "call_id": "call_aaa111", "name": "transfer_to_x", "arguments": "{}"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
        {"type": "function_call_output", "call_id": "call_aaa111", "output": "ok"},
    ]
    ops = route_items(
        items, ledger=ledger, session_id="sess-1", start_index=0, timestamp=_ts()
    )

    # Expected: DualAppend(Started), SingleAppend(body), DualAppend(Completed)
    assert len(ops) == 3
    assert isinstance(ops[0], DualAppend) and isinstance(ops[0].event, SubagentStarted)
    assert isinstance(ops[1], SingleAppend)
    assert isinstance(ops[1].events[0], AssistantTextGenerated)
    assert isinstance(ops[2], DualAppend) and isinstance(ops[2].event, SubagentCompleted)
```

- [ ] **Step 6: Run tests to verify failure**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: FAIL with `ImportError: cannot import name 'route_items' from 'kurrent_openai_agents._handoffs'`.

- [ ] **Step 7: Implement `route_items`, `SingleAppend`, `DualAppend`**

Append to `openai-agents/python/kurrent_openai_agents/_handoffs.py`:

```python
from datetime import datetime
from typing import Union

from google.protobuf.message import Message as ProtoMessage

from ._codec import items_to_canonical
from ._openai_events import OpenAIItem
from ._stream_names import for_subsession


@dataclass(slots=True)
class SingleAppend:
    """Append a batch of events to a single stream."""

    stream: str
    events: list[ProtoMessage | OpenAIItem]


@dataclass(slots=True)
class DualAppend:
    """Atomic dual-stream append of ONE event to TWO streams.

    Used exclusively for ``SubagentStarted`` / ``SubagentCompleted`` per
    SCHEMA_v2 §3.5. The session.add_items writer dispatches this via
    ``multi_append_to_stream`` with two ``NewEvents`` entries.
    """

    streams: tuple[str, str]
    event: ProtoMessage


WriteOp = Union[SingleAppend, DualAppend]


def route_items(
    items: list[dict[str, Any]],
    *,
    ledger: _HandoffLedger,
    session_id: str,
    start_index: int,
    timestamp: datetime,
) -> list[WriteOp]:
    """Walk a flat dict list, emitting an ordered list of write operations.

    Mutates ``ledger`` in place: consumes ``expected``, populates / clears
    ``active``, flips ``current_owner`` on handoff start / end. The returned
    list preserves the write order — the caller MUST honour it so subagent
    transcript items land on the subsession stream **after** the
    ``SubagentStarted`` lifecycle marker.

    Handoff promotion happens **only** when ``ledger.expected`` matches the
    next ``function_call`` (start guard) or ``ledger.active`` matches a
    ``function_call_output``'s ``call_id`` (completion guard). Without those,
    handoff-shaped items fall through to the normal codec (becoming
    ``OpenAIItem`` for the framework-specific fallback) — see spec
    "Hooks not wired" degradation.
    """
    ops: list[WriteOp] = []
    pending: list[ProtoMessage | OpenAIItem] = []
    pending_stream: str | None = None

    def flush_pending() -> None:
        nonlocal pending, pending_stream
        if pending and pending_stream is not None:
            ops.append(SingleAppend(stream=pending_stream, events=list(pending)))
        pending = []
        pending_stream = None

    def queue_single(stream: str, events: list[ProtoMessage | OpenAIItem]) -> None:
        nonlocal pending_stream
        if not events:
            return
        if pending_stream is None:
            pending_stream = stream
            pending.extend(events)
        elif pending_stream == stream:
            pending.extend(events)
        else:
            flush_pending()
            pending_stream = stream
            pending.extend(events)

    for offset, item in enumerate(items):
        message_index = start_index + offset
        kind = item.get("type")

        if kind == "function_call" and ledger.expected is not None:
            # SubagentStarted: flush any pending single-stream writes first
            # so they land BEFORE the lifecycle marker, then emit DualAppend.
            flush_pending()
            evt = _emit_subagent_started(item, ledger, session_id, message_index, timestamp)
            ops.append(DualAppend(
                streams=(ledger.parent_stream, ledger.current_owner),  # current_owner has flipped
                event=evt,
            ))
            continue

        if kind == "function_call_output":
            call_id = item.get("call_id") or ""
            if call_id in ledger.active:
                subsession = ledger.active[call_id].subsession_stream
                flush_pending()
                evt = _emit_subagent_completed(item, ledger, message_index, timestamp)
                del ledger.active[call_id]
                ledger.current_owner = ledger.parent_stream
                ops.append(DualAppend(streams=(ledger.parent_stream, subsession), event=evt))
                continue

        # Regular item — route a canonical event (or OpenAIItem) to the active owner.
        canonical_events = items_to_canonical([item], start_index=message_index, timestamp=timestamp)
        queue_single(ledger.current_owner, canonical_events)

    flush_pending()
    return ops


def _emit_subagent_started(
    item: dict[str, Any],
    ledger: _HandoffLedger,
    session_id: str,
    message_index: int,
    timestamp: datetime,
) -> ProtoMessage:
    """Build a ``SubagentStarted`` for the matched handoff_call and update the ledger.

    Defers proto field-setting + ``extensions.openai`` payload to ``_codec``.
    """
    from ._codec import _map_handoff_call  # local import to avoid module-load cycles

    expected = ledger.expected
    assert expected is not None  # caller guards
    call_id = item.get("call_id") or ""
    agent_id = derive_agent_id(expected.to_name, call_id)
    subsession_stream = for_subsession(session_id, agent_id)

    evt = _map_handoff_call(
        item=item,
        agent_id=agent_id,
        agent_type=expected.to_name,
        source_agent=expected.from_name,
        subsession_stream=subsession_stream,
        message_index=message_index,
        timestamp=timestamp,
    )

    ledger.active[call_id] = ActiveHandoff(
        call_id=call_id, agent_id=agent_id, agent_type=expected.to_name,
        subsession_stream=subsession_stream,
    )
    ledger.current_owner = subsession_stream
    ledger.expected = None
    return evt


def _emit_subagent_completed(
    item: dict[str, Any],
    ledger: _HandoffLedger,
    message_index: int,
    timestamp: datetime,
) -> ProtoMessage:
    from ._codec import _map_handoff_output  # local import to avoid module-load cycles

    call_id = item["call_id"]
    active = ledger.active[call_id]
    return _map_handoff_output(
        item=item, agent_id=active.agent_id,
        message_index=message_index, timestamp=timestamp,
    )
```

- [ ] **Step 8: Run tests** (will still fail — `_map_handoff_call` / `_map_handoff_output` not yet in `_codec.py`)

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: FAIL with `ImportError: cannot import name '_map_handoff_call' from 'kurrent_openai_agents._codec'`. Continue to Task 4 to fix.

- [ ] **Step 9: Do NOT commit yet** — Task 4 completes the codec side; commit at end of Task 4.

---

## Task 4: `_codec.py` — handoff_call → SubagentStarted, handoff_output → SubagentCompleted

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/_codec.py`

- [ ] **Step 1: Add `_map_handoff_call` and `_map_handoff_output` mappers**

Open `openai-agents/python/kurrent_openai_agents/_codec.py`. Add imports for `SubagentStarted` / `SubagentCompleted` to the `from kurrent_agent_schema import (...)` block:

```python
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantThinkingGenerated,
    AssistantToolCallsGenerated,
    InterruptIssued,
    InterruptResolved,
    SubagentCompleted,
    SubagentStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
)
```

After the existing `_map_mcp_approval_response` function, add:

```python
def _map_handoff_call(
    *,
    item: dict[str, Any],
    agent_id: str,
    agent_type: str,
    source_agent: str,
    subsession_stream: str,
    message_index: int,
    timestamp: datetime,
) -> SubagentStarted:
    """Map an OpenAI ``handoff_call`` (a ``function_call`` whose target is a
    subagent) to canonical ``SubagentStarted``.

    The ``agent_id`` is precomputed by ``_handoffs.derive_agent_id`` so this
    mapper stays a pure formatter — no slug logic leaks into the codec.
    SCHEMA_v2 §3.5.
    """
    evt = SubagentStarted(
        agent_id=agent_id,
        agent_type=agent_type,
        subsession_stream=subsession_stream,
    )
    prompt = item.get("arguments")
    if isinstance(prompt, str) and prompt:
        evt.prompt = prompt
    evt.timestamp.FromDatetime(timestamp.replace(tzinfo=None))
    _set_openai_extension(evt, {
        "raw_item": dict(item),
        "item_type": "handoff_call",
        "handoff": {"source_agent": source_agent},
    })
    del message_index  # SubagentStarted has no message_index field; reserved for future
    return evt


def _map_handoff_output(
    *,
    item: dict[str, Any],
    agent_id: str,
    message_index: int,
    timestamp: datetime,
) -> SubagentCompleted:
    """Map an OpenAI ``handoff_output`` (the synthetic ack for a handoff) to
    canonical ``SubagentCompleted``.

    Outcome defaults to ``"success"`` — the SDK doesn't surface error/cancel
    state on the handoff_output dict. ``summary`` is the truncated text view
    of the output; the full original rides under ``extensions.openai.raw_item``.
    """
    summary = _serialize_output(item.get("output")) or ""
    summary = summary[:512]
    evt = SubagentCompleted(agent_id=agent_id, outcome="success")
    if summary:
        evt.summary = summary
    evt.timestamp.FromDatetime(timestamp.replace(tzinfo=None))
    _set_openai_extension(evt, {
        "raw_item": dict(item),
        "item_type": "handoff_output",
    })
    del message_index
    return evt
```

- [ ] **Step 2: Add round-trip support for `SubagentStarted` / `SubagentCompleted` in `canonical_to_items`**

In the same file, find `canonical_to_items` (around line 102). The current logic prefers `extensions.openai.raw_item`; that already handles the lifecycle events generically because they carry `raw_item`. Verify by reading the function — no change needed if `raw_item` is the only path.

Add a safety net: add `SubagentStarted` and `SubagentCompleted` cases to `_fallback_reconstruct` (around line 388) so cross-framework reads (another framework wrote a subagent lifecycle without `extensions.openai.raw_item`) can still synthesise a minimal `function_call` / `function_call_output` for OpenAI's flat replay:

```python
    if isinstance(event, SubagentStarted):
        return {
            "type": "function_call",
            "call_id": event.agent_id,  # best-effort: no original call_id available
            "name": f"transfer_to_{event.agent_type.lower()}" if event.HasField("agent_type") else "transfer",
            "arguments": event.prompt if event.HasField("prompt") else "{}",
        }
    if isinstance(event, SubagentCompleted):
        return {
            "type": "function_call_output",
            "call_id": event.agent_id,
            "output": event.summary if event.HasField("summary") else "",
        }
```

(Add these two cases before the final `return None` in `_fallback_reconstruct`.)

- [ ] **Step 3: Run all handoff tests**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: PASS (the test cases from Task 3 step 5 — slug, derive_agent_id, route_items).

- [ ] **Step 4: Run the full suite to confirm no regressions**

Run: `cd openai-agents/python && uv run pytest -q`
Expected: PASS — all pre-existing 83 tests plus the new handoff tests.

- [ ] **Step 5: Commit Tasks 3 + 4 together**

```bash
git add openai-agents/python/kurrent_openai_agents/_handoffs.py openai-agents/python/kurrent_openai_agents/_codec.py openai-agents/python/tests/test_handoffs.py
git commit -m "$(cat <<'EOF'
feat(openai-agents): _handoffs module + codec mappers for SubagentStarted/Completed (AI-471)

Adds the pure-logic _handoffs.py (slug, derive_agent_id, ledger
dataclasses, route_items splitter) and the corresponding _codec
mappers (_map_handoff_call, _map_handoff_output) that build canonical
subagent lifecycle events with extensions.openai.raw_item preserved
for lossless round-trip. session.py integration follows in subsequent
commits.
EOF
)"
```

---

## Task 5: `serialize_for_multi_append` — string-only metadata for v2 multi-append path

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/_serialization.py`
- Test: `openai-agents/python/tests/test_serialization.py` (create if absent)

- [ ] **Step 1: Write failing tests**

Check if `openai-agents/python/tests/test_serialization.py` exists. If yes, append; otherwise create with:

```python
"""Tests for the OpenAI Agents serialization helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from kurrent_agent_schema import SubagentStarted

from kurrent_openai_agents._serialization import serialize, serialize_for_multi_append


def _make_started() -> SubagentStarted:
    evt = SubagentStarted(agent_id="sub-x-abc", agent_type="X")
    evt.timestamp.FromDatetime(datetime(2026, 5, 14, 12, 0, 0))
    return evt


def test_single_stream_serialize_stamps_schema_version_as_int() -> None:
    new_event = serialize(_make_started())
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2
    assert isinstance(metadata["$schema_version"], int)


def test_multi_append_serialize_stamps_schema_version_as_string() -> None:
    new_event = serialize_for_multi_append(_make_started())
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == "2"
    assert isinstance(metadata["$schema_version"], str)


def test_multi_append_rejects_non_string_metadata_values() -> None:
    with pytest.raises(ValueError, match="multi-append metadata"):
        serialize_for_multi_append(_make_started(), metadata={"latency_ms": 42})


def test_multi_append_passes_through_string_metadata() -> None:
    new_event = serialize_for_multi_append(
        _make_started(), metadata={"source": "test"}
    )
    metadata = json.loads(new_event.metadata)
    assert metadata["source"] == "test"
    assert metadata["$schema_version"] == "2"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd openai-agents/python && uv run pytest tests/test_serialization.py -v`
Expected: FAIL with `ImportError: cannot import name 'serialize_for_multi_append'`.

- [ ] **Step 3: Implement `serialize_for_multi_append`**

Open `openai-agents/python/kurrent_openai_agents/_serialization.py`. After the existing `serialize` function, add:

```python
def serialize_for_multi_append(
    event: ProtoMessage | BaseModel,
    *,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> NewEvent:
    """Serialize for the v2 multi-stream append path.

    KurrentDB's gRPC ``AppendSession`` (used by ``multi_append_to_stream``)
    requires event metadata to be a JSON document with **string-only values**
    (see ``kurrentdbclient.v2streams._metadata_to_properties``). The single-
    stream ``append_to_stream`` path accepts any JSON, so the regular
    ``serialize`` stamps ``$schema_version`` as int ``2``. This helper stamps
    it as the string ``"2"`` and rejects any caller-supplied non-string values.
    """
    data = _encode_event_data(event)

    effective: dict[str, Any] = dict(metadata) if metadata else {}
    for key, value in effective.items():
        if not isinstance(value, str):
            raise ValueError(
                f"multi-append metadata values must be strings; got "
                f"{key}={value!r} ({type(value).__name__})"
            )
    effective[SCHEMA_VERSION_METADATA_KEY] = str(SCHEMA_VERSION)
    metadata_bytes = json.dumps(effective, separators=(",", ":")).encode("utf-8")

    return NewEvent(
        id=event_id or uuid.uuid4(),
        type=_name_for(event),
        data=data,
        metadata=metadata_bytes,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd openai-agents/python && uv run pytest tests/test_serialization.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/_serialization.py openai-agents/python/tests/test_serialization.py
git commit -m "$(cat <<'EOF'
feat(openai-agents): add serialize_for_multi_append for v2 multi-stream path (AI-471)

KurrentDB's v2 AppendSession requires JSON metadata with string-only
values. Adds a paired serializer that coerces $schema_version to "2"
and rejects non-string caller metadata, leaving the existing single-
stream serializer's int-typed metadata untouched.
EOF
)"
```

---

## Task 6: `KurrentDBSession` — inherit `RunHooksBase`, add ledger + hook overrides

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/session.py`
- Test: `openai-agents/python/tests/test_handoffs.py` (extend)

- [ ] **Step 1: Write failing tests for hook lifecycle**

Append to `openai-agents/python/tests/test_handoffs.py`:

```python
# ----- KurrentDBSession as RunHooksBase ----------------------------------

from agents.lifecycle import RunHooksBase

from kurrent_openai_agents import KurrentDBSession


class _StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name


def test_session_is_a_run_hooks_base() -> None:
    # Compose without a real KurrentDB client — we only test the in-memory ledger.
    session = KurrentDBSession(session_id="sess-1", client=None)  # type: ignore[arg-type]
    assert isinstance(session, RunHooksBase)


@pytest.mark.asyncio
async def test_on_handoff_sets_expected_handoff() -> None:
    session = KurrentDBSession(session_id="sess-1", client=None)  # type: ignore[arg-type]
    target = _StubAgent("ResearchAgent")
    await session.on_handoff(context=None, from_agent=_StubAgent("Triage"), to_agent=target)
    assert session._ledger.expected is not None
    assert session._ledger.expected.from_name == "Triage"
    assert session._ledger.expected.to_name == "ResearchAgent"
    assert session._ledger.expected.to_agent is target


@pytest.mark.asyncio
async def test_duplicate_on_handoff_is_idempotent_while_expected_set() -> None:
    session = KurrentDBSession(session_id="sess-1", client=None)  # type: ignore[arg-type]
    a = _StubAgent("A"); b = _StubAgent("B")
    await session.on_handoff(context=None, from_agent=a, to_agent=b)
    first = session._ledger.expected
    await session.on_handoff(context=None, from_agent=a, to_agent=b)
    # Second call is dropped while expected is still pending.
    assert session._ledger.expected is first
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: FAIL — `KurrentDBSession` doesn't currently inherit `RunHooksBase`, and `on_handoff` doesn't set the ledger.

- [ ] **Step 3: Update `KurrentDBSession` to inherit both interfaces and add ledger**

Open `openai-agents/python/kurrent_openai_agents/session.py`. Replace the import block and the class definition. The current class signature is `class KurrentDBSession(SessionABC):` — change it to inherit `RunHooksBase` too. Add the ledger field and the hook overrides. The relevant patch:

At the top of the file, add:

```python
from agents.lifecycle import RunHooksBase

from ._handoffs import ExpectedHandoff, _HandoffLedger
```

Change the class declaration from:

```python
class KurrentDBSession(SessionABC):
```

to:

```python
class KurrentDBSession(SessionABC, RunHooksBase):
```

In `__init__`, after `self._stream = for_session(session_id)`, append:

```python
        self._ledger = _HandoffLedger(parent_stream=self._stream)
```

After the existing `clear_session` method, add the hook overrides:

```python
    # ----- RunHooksBase overrides -------------------------------------------

    async def on_handoff(
        self,
        context: Any,
        from_agent: Any,
        to_agent: Any,
    ) -> None:
        """Stash the expected handoff target until the next ``function_call`` arrives.

        Idempotent while ``expected`` is already pending — duplicate hook delivery
        from streaming retries or replay doesn't double-emit ``SubagentStarted``.
        """
        if self._ledger.expected is not None:
            return
        self._ledger.expected = ExpectedHandoff(
            from_name=getattr(from_agent, "name", "") or "",
            to_name=getattr(to_agent, "name", "") or "",
            to_agent=to_agent,
        )

    async def on_agent_end(
        self,
        context: Any,
        agent: Any,
        output: Any,
    ) -> None:
        """Emit a deferred ``SubagentCompleted`` if this agent's subagent never returned.

        Some flows let the target agent produce a final output without handing
        back to the parent; without this catch we'd leak an open subagent.
        """
        agent_name = getattr(agent, "name", "") or ""
        for call_id, active in list(self._ledger.active.items()):
            if active.agent_type == agent_name and self._ledger.current_owner == active.subsession_stream:
                await self._emit_deferred_subagent_completed(call_id, output)
                return
```

Add the helper at the bottom of the class (it's a no-op stub for now; Task 7 will implement the actual KurrentDB writes):

```python
    async def _emit_deferred_subagent_completed(
        self, call_id: str, output: Any
    ) -> None:
        """Implementation lands in Task 7 once add_items has the multi-append wiring."""
        # Placeholder kept to satisfy on_agent_end's call site; Task 7 fills in.
        del call_id, output
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py -v`
Expected: PASS — the three new tests + all previous handoff tests.

- [ ] **Step 5: Confirm the full suite stays green**

Run: `cd openai-agents/python && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/session.py openai-agents/python/tests/test_handoffs.py
git commit -m "$(cat <<'EOF'
feat(openai-agents): KurrentDBSession implements RunHooksBase (AI-471)

Makes KurrentDBSession a single object that is both SessionABC and
RunHooksBase, used as Runner.run(..., session=s, hooks=s). on_handoff
stashes the next-handoff target in a per-session _HandoffLedger;
duplicate hook delivery while expected is set is dropped. on_agent_end
hooks the no-return-handoff case (write path lands in the next commit).
EOF
)"
```

---

## Task 7: `KurrentDBSession.add_items` — route via ledger + atomic dual-stream multi-append

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/session.py`

- [ ] **Step 1: Replace `add_items` body to walk the WriteOp list**

In `openai-agents/python/kurrent_openai_agents/session.py`, replace the existing `add_items` method with:

```python
    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Append items to the session stream(s), routing handoff-target items
        to ``AgentSubsession-{parent}-{agent_id}`` per SCHEMA_v2 §3.5."""
        if not items:
            return

        start_index = await self._count_items()

        from ._handoffs import DualAppend, SingleAppend, route_items

        ops = route_items(
            [dict(item) for item in items],  # type: ignore[arg-type]
            ledger=self._ledger,
            session_id=self.session_id,
            start_index=start_index,
            timestamp=datetime.now(UTC),
        )
        if not ops:
            return

        if start_index == 0:
            await self._emit_session_started_if_missing()

        await self._write_ops(ops)
```

Add the new helper below in the same class:

```python
    async def _write_ops(self, ops: list[Any]) -> None:
        """Walk the route_items output in order, dispatching each op.

        ``SingleAppend`` → ``append_to_stream`` with int-typed metadata
        (existing serializer). ``DualAppend`` → atomic
        ``multi_append_to_stream`` with string-typed metadata
        (``serialize_for_multi_append``) so the v2 multi-append's
        string-only constraint is satisfied. Ordering is preserved exactly
        as route_items emitted, so subagent body items always land after
        their ``SubagentStarted`` lifecycle marker.
        """
        from kurrentdbclient import NewEvents

        from ._handoffs import DualAppend, SingleAppend

        for op in ops:
            if isinstance(op, SingleAppend):
                new_events = [_serialization.serialize(evt) for evt in op.events]
                await self._client.append_to_stream(
                    op.stream,
                    events=new_events,
                    current_version=StreamState.ANY,
                )
            elif isinstance(op, DualAppend):
                parent_stream, sub_stream = op.streams
                await self._client.multi_append_to_stream([
                    NewEvents(
                        stream_name=parent_stream,
                        events=[_serialization.serialize_for_multi_append(op.event)],
                        current_version=StreamState.ANY,
                    ),
                    NewEvents(
                        stream_name=sub_stream,
                        events=[_serialization.serialize_for_multi_append(op.event)],
                        current_version=StreamState.ANY,
                    ),
                ])
            else:
                logger.error("Unknown WriteOp type %r — dropping", type(op).__name__)
```

Update the existing imports near the top:

```python
from kurrent_agent_schema import (
    USAGE_METADATA_KEY,
    SessionContinuedAs,
    SessionEnded,
    SessionStarted,
    SubagentCompleted,
    SubagentStarted,
)
```

(Already present per the AI-475 cutover — no change needed if already there.)

- [ ] **Step 2: Implement `_emit_deferred_subagent_completed` (was a stub in Task 6)**

Replace the placeholder added in Task 6 step 3 with:

```python
    async def _emit_deferred_subagent_completed(
        self, call_id: str, output: Any
    ) -> None:
        """Emit a SubagentCompleted to BOTH streams when the target ended
        without producing a handoff_output (one-way handoff)."""
        from ._codec import _map_handoff_output
        from kurrentdbclient import NewEvents

        active = self._ledger.active.pop(call_id, None)
        if active is None:
            return
        synthetic_item = {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output if isinstance(output, str) else str(output or ""),
        }
        evt = _map_handoff_output(
            item=synthetic_item, agent_id=active.agent_id,
            message_index=-1, timestamp=datetime.now(UTC),
        )
        await self._client.multi_append_to_stream([
            NewEvents(
                stream_name=self._stream,
                events=[_serialization.serialize_for_multi_append(evt)],
                current_version=StreamState.ANY,
            ),
            NewEvents(
                stream_name=active.subsession_stream,
                events=[_serialization.serialize_for_multi_append(evt)],
                current_version=StreamState.ANY,
            ),
        ])
        self._ledger.current_owner = self._stream
```

- [ ] **Step 3: Run unit + serialization tests to confirm no regressions**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py tests/test_serialization.py tests/test_codec.py -q`
Expected: PASS (existing handoff + codec tests still pass; the write path's effect is exercised by Task 9's integration tests).

- [ ] **Step 4: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/session.py
git commit -m "$(cat <<'EOF'
feat(openai-agents): route add_items via _HandoffLedger and atomic dual-stream multi-append (AI-471)

add_items now consults the ledger via route_items, splitting items
into per-stream segments. SubagentStarted / SubagentCompleted go
through serialize_for_multi_append + multi_append_to_stream so the
parent and subsession streams update atomically — matching Capacitor's
projection expectation. _emit_deferred_subagent_completed closes the
one-way-handoff case (target ends without a handoff_output).
EOF
)"
```

---

## Task 8: `KurrentDBSession.get_items` — re-flatten from subsession streams

**Files:**
- Modify: `openai-agents/python/kurrent_openai_agents/session.py`

- [ ] **Step 1: Replace `get_items` to inline subsession transcripts**

In `openai-agents/python/kurrent_openai_agents/session.py`, replace the existing `get_items` method with:

```python
    async def get_items(
        self, limit: int | None = None
    ) -> list[TResponseInputItem]:
        """Retrieve conversation history, newest-last, re-flattening subagent
        transcripts inline at each ``SubagentStarted`` marker per spec §3."""
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return []

        items: list[TResponseInputItem] = []
        subsession_cache: dict[str, list[Any]] = {}

        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue

            if isinstance(event, SubagentStarted):
                # Emit the original handoff_call dict from extensions.openai.raw_item.
                handoff_call_items = canonical_to_items([event])
                items.extend(handoff_call_items)  # type: ignore[arg-type]

                # Inline the subsession transcript.
                subsession_stream = event.subsession_stream if event.HasField("subsession_stream") else ""
                if subsession_stream:
                    sub_items = await self._read_subsession(subsession_stream, subsession_cache)
                    items.extend(sub_items)
                else:
                    logger.warning(
                        "SubagentStarted without subsession_stream on %s — skipping inline transcript",
                        self._stream,
                    )
                continue

            if isinstance(event, SubagentCompleted):
                handoff_output_items = canonical_to_items([event])
                items.extend(handoff_output_items)  # type: ignore[arg-type]
                continue

            if isinstance(event, _LIFECYCLE_PROTO_TYPES):
                continue

            items.extend(canonical_to_items([event]))  # type: ignore[arg-type]

        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items
```

Add the helper below:

```python
    async def _read_subsession(
        self,
        subsession_stream: str,
        cache: dict[str, list[Any]],
    ) -> list[TResponseInputItem]:
        """Read a subsession stream, skipping its mirrored Subagent* lifecycle
        and converting remaining events to OpenAI flat dicts."""
        if subsession_stream in cache:
            return cache[subsession_stream]  # type: ignore[return-value]

        try:
            recorded = await self._client.get_stream(subsession_stream)
        except NotFoundError:
            logger.warning(
                "Missing subsession stream %s referenced from %s — emitting handoff_call only",
                subsession_stream, self._stream,
            )
            cache[subsession_stream] = []
            return []

        canonical_events: list[Any] = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if event is None:
                continue
            # Skip the mirrored SubagentStarted/Completed copies — the parent
            # stream already accounts for those.
            if isinstance(event, (SubagentStarted, SubagentCompleted)):
                continue
            canonical_events.append(event)

        sub_items = canonical_to_items(canonical_events)
        cache[subsession_stream] = sub_items  # type: ignore[assignment]
        return sub_items  # type: ignore[return-value]
```

Remove `SubagentStarted` and `SubagentCompleted` from `_LIFECYCLE_PROTO_TYPES` and from `_LIFECYCLE_EVENT_TYPES` (since `get_items` now handles them explicitly above):

```python
# Lifecycle / out-of-conversation event types that get_items must skip.
_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset({
    "SessionStarted",
    "SessionEnded",
    "SessionContinuedAs",
})

_LIFECYCLE_PROTO_TYPES: tuple[type, ...] = (
    SessionStarted,
    SessionEnded,
    SessionContinuedAs,
)
```

Also update `_count_items` to still skip `SubagentStarted` / `SubagentCompleted` for index correctness — they don't occupy an "item" slot from the SDK's perspective:

```python
    async def _count_items(self) -> int:
        try:
            recorded = await self._client.get_stream(self._stream)
        except NotFoundError:
            return 0
        skip = _LIFECYCLE_EVENT_TYPES | {"SubagentStarted", "SubagentCompleted"}
        return sum(1 for r in recorded if r.type not in skip)
```

- [ ] **Step 2: Confirm the existing test_session.py suite stays green**

Run: `cd openai-agents/python && uv run pytest tests/test_session.py -q`
Expected: PASS — basic Session operations don't exercise subagent flow yet.

- [ ] **Step 3: Run full unit / codec / serialization suite**

Run: `cd openai-agents/python && uv run pytest tests/test_handoffs.py tests/test_codec.py tests/test_serialization.py tests/test_session.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add openai-agents/python/kurrent_openai_agents/session.py
git commit -m "$(cat <<'EOF'
feat(openai-agents): re-flatten subsession streams on get_items (AI-471)

get_items now reads the parent stream, inlines each subsession
transcript at its SubagentStarted marker, and emits the original
handoff_call/handoff_output dicts from extensions.openai.raw_item.
Per-call caching avoids re-reading the same subsession stream within
one get_items invocation. Missing subsession streams degrade
gracefully with a warning.
EOF
)"
```

---

## Task 9: Integration test — end-to-end handoff happy path

**Files:**
- Create: `openai-agents/python/tests/test_handoff_session.py`

- [ ] **Step 1: Add the happy-path integration test**

Create `openai-agents/python/tests/test_handoff_session.py`:

```python
"""Integration tests for OpenAI Agents handoff promotion (AI-471).

Exercises the full flow: Runner.run with two agents that hand off, against
a real KurrentDB Testcontainer. Asserts:
- parent stream contains the canonical SubagentStarted/Completed lifecycle,
- subsession stream is created and mirrors the lifecycle,
- get_items returns a flat list the SDK can replay verbatim.
"""

from __future__ import annotations

import uuid

import pytest
from agents import Agent, Runner
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_openai_agents import KurrentDBSession
from kurrent_openai_agents._serialization import deserialize
from kurrent_openai_agents._stream_names import for_session


pytestmark = pytest.mark.asyncio


async def _stream_events(client: AsyncKurrentDBClient, stream: str) -> list:
    try:
        recorded = await client.get_stream(stream)
    except Exception:
        return []
    out = []
    for r in recorded:
        evt = deserialize(r)
        if evt is not None:
            out.append((r.type, evt))
    return out


async def test_handoff_emits_subagent_lifecycle_to_both_streams(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client, app_name="test")

    spec = Agent(name="Specialist", instructions="Respond with the word DONE.")
    triage = Agent(
        name="Triage", instructions="Always hand off to Specialist.",
        handoffs=[spec],
    )
    result = await Runner.run(
        triage,
        "Investigate this for me.",
        session=session, hooks=session,
    )
    assert result is not None

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    parent_types = [t for t, _ in parent_events]
    assert "SessionStarted" in parent_types
    assert "SubagentStarted" in parent_types
    assert "SubagentCompleted" in parent_types

    started = next(evt for t, evt in parent_events if t == "SubagentStarted")
    assert started.agent_type == "Specialist"
    subsession_stream = started.subsession_stream
    assert subsession_stream.startswith(f"AgentSubsession-{session_id}-sub-specialist-")

    sub_events = await _stream_events(kurrentdb_client, subsession_stream)
    sub_types = [t for t, _ in sub_events]
    # Subsession carries the mirrored lifecycle and at least one canonical
    # conversation event from the specialist's turn.
    assert sub_types.count("SubagentStarted") == 1
    assert sub_types.count("SubagentCompleted") == 1
    assert any(t == "AssistantTextGenerated" for t in sub_types)


async def test_get_items_inlines_subagent_transcript(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    spec = Agent(name="Specialist", instructions="Respond DONE.")
    triage = Agent(name="Triage", instructions="Hand off.", handoffs=[spec])
    await Runner.run(triage, "Go.", session=session, hooks=session)

    # Fresh session over the same stream should see the flat replay.
    replay = KurrentDBSession(session_id=session_id, client=kurrentdb_client)
    items = await replay.get_items()
    types = [it.get("type") for it in items]
    # The original handoff_call and handoff_output dicts are emitted from
    # extensions.openai.raw_item; the specialist's turn appears between them.
    assert "function_call" in types  # the handoff_call
    assert "function_call_output" in types  # the handoff_output
    assert any(t == "message" for t in types)  # specialist's response
```

- [ ] **Step 2: Run the test**

Run: `cd openai-agents/python && uv run pytest tests/test_handoff_session.py::test_handoff_emits_subagent_lifecycle_to_both_streams -v`
Expected: PASS. (Note: this test spins up a real Testcontainer + makes a real LLM call. It requires `OPENAI_API_KEY` in the environment. If you don't have one, see Task 10 step 1 for the mocked-model variant.)

If `OPENAI_API_KEY` is unavailable, skip this assertion temporarily and rely on Task 10's mocked variant.

- [ ] **Step 3: Run both tests to confirm**

Run: `cd openai-agents/python && uv run pytest tests/test_handoff_session.py -v`
Expected: PASS (both happy-path cases).

- [ ] **Step 4: Commit**

```bash
git add openai-agents/python/tests/test_handoff_session.py
git commit -m "$(cat <<'EOF'
test(openai-agents): end-to-end handoff happy-path integration (AI-471)

Drives Runner.run with two Agents that hand off and asserts canonical
SubagentStarted/Completed lifecycle on both the parent and subsession
streams, plus get_items flat-replay of the original handoff dicts.
EOF
)"
```

---

## Task 10: Integration tests — degradation, duplicate-hook, missing subsession

**Files:**
- Modify: `openai-agents/python/tests/test_handoff_session.py` (extend)

- [ ] **Step 1: Add the one-way-handoff test (uses on_agent_end deferred completion)**

Append to `openai-agents/python/tests/test_handoff_session.py`:

```python
from datetime import UTC, datetime

from kurrent_agent_schema import SubagentCompleted, SubagentStarted
from kurrentdbclient import NewEvents, StreamState

from kurrent_openai_agents._codec import _map_handoff_call
from kurrent_openai_agents._handoffs import ActiveHandoff, ExpectedHandoff
from kurrent_openai_agents._serialization import serialize_for_multi_append
from kurrent_openai_agents._stream_names import for_subsession


async def test_on_agent_end_emits_subagent_completed_for_unreturned_subagent(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    # Manually seed the ledger as if a handoff had started but no return.
    call_id = "call_unreturned_abc"
    agent_id = "sub-loner-ned_abc"
    sub_stream = for_subsession(session_id, agent_id)
    session._ledger.active[call_id] = ActiveHandoff(
        call_id=call_id, agent_id=agent_id, agent_type="Loner",
        subsession_stream=sub_stream,
    )
    session._ledger.current_owner = sub_stream

    class _Agent: name = "Loner"
    await session.on_agent_end(context=None, agent=_Agent(), output="all done")

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    completed = [evt for t, evt in parent_events if t == "SubagentCompleted"]
    assert len(completed) == 1
    assert completed[0].agent_id == agent_id
    assert session._ledger.current_owner.endswith(for_session(session_id))
    assert call_id not in session._ledger.active


async def test_hooks_not_wired_falls_through_to_openaiitem(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """Without hooks=session, handoff dicts persist as OpenAIItem, no Subagent* emitted."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    handoff_call = {
        "type": "function_call",
        "call_id": "call_z123456",
        "name": "transfer_to_x",
        "arguments": "{}",
    }
    handoff_output = {
        "type": "function_call_output",
        "call_id": "call_z123456",
        "output": "{}",
    }
    await session.add_items([handoff_call, handoff_output])  # type: ignore[list-item]

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    types = [t for t, _ in parent_events]
    assert "SubagentStarted" not in types
    assert "SubagentCompleted" not in types
    assert "OpenAIItem" in types

    # No subsession stream should have been created.
    sub_events = await _stream_events(kurrentdb_client, for_subsession(session_id, "any"))
    assert sub_events == []


async def test_get_items_handles_missing_subsession_stream(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """Parent has SubagentStarted but the subsession stream is gone — emit handoff_call only."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    # Emit a SubagentStarted whose subsession_stream points nowhere.
    fake_call = {
        "type": "function_call", "call_id": "call_ghost1",
        "name": "transfer_to_ghost", "arguments": "{}",
    }
    started = _map_handoff_call(
        item=fake_call, agent_id="sub-ghost-host1", agent_type="Ghost",
        source_agent="Triage",
        subsession_stream=for_subsession(session_id, "sub-ghost-host1"),
        message_index=0, timestamp=datetime.now(UTC),
    )
    # Write only to parent — simulate a non-atomic legacy writer / crash.
    await kurrentdb_client.append_to_stream(
        for_session(session_id),
        events=[serialize_for_multi_append(started)],
        current_version=StreamState.ANY,
    )

    items = await session.get_items()
    types = [it.get("type") for it in items]
    assert "function_call" in types  # handoff_call reconstructed from raw_item
    # No transcript items (the subsession was missing).
    assert "message" not in types


async def test_duplicate_on_handoff_emits_single_subagent_started(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(session_id=session_id, client=kurrentdb_client)

    class _A: name = "A"
    class _B: name = "B"
    await session.on_handoff(context=None, from_agent=_A(), to_agent=_B())
    await session.on_handoff(context=None, from_agent=_A(), to_agent=_B())  # duplicate

    # Drain the ledger via a synthesised handoff_call.
    call = {
        "type": "function_call", "call_id": "call_dup123456",
        "name": "transfer_to_b", "arguments": "{}",
    }
    await session.add_items([call])  # type: ignore[list-item]

    parent_events = await _stream_events(kurrentdb_client, for_session(session_id))
    started_count = sum(1 for t, _ in parent_events if t == "SubagentStarted")
    assert started_count == 1
```

- [ ] **Step 2: Run the new tests**

Run: `cd openai-agents/python && uv run pytest tests/test_handoff_session.py -v --timeout=120`
Expected: PASS (all 4 tests in this task plus the 2 from Task 9). pytest-timeout may not be installed; if so, drop the flag.

- [ ] **Step 3: Run the full suite to confirm no regressions**

Run: `cd openai-agents/python && uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add openai-agents/python/tests/test_handoff_session.py
git commit -m "$(cat <<'EOF'
test(openai-agents): degradation + duplicate-hook + missing-subsession coverage (AI-471)

Covers the four non-happy-path scenarios from the spec: one-way
handoff (on_agent_end deferred completion), hooks-not-wired
fall-through to OpenAIItem, missing subsession stream during
get_items, and duplicate on_handoff idempotency.
EOF
)"
```

---

## Task 11: Codec fixtures for `extensions.openai` shape

**Files:**
- Create: `openai-agents/python/tests/fixtures/__init__.py`
- Create: `openai-agents/python/tests/fixtures/subagent_started_openai.json`
- Create: `openai-agents/python/tests/fixtures/subagent_completed_openai.json`
- Modify: `openai-agents/python/tests/test_codec.py`

- [ ] **Step 1: Create the fixtures**

Create `openai-agents/python/tests/fixtures/__init__.py` (empty):

```python
```

Create `openai-agents/python/tests/fixtures/subagent_started_openai.json`:

```json
{
  "agent_id": "sub-specialist-abc123",
  "agent_type": "Specialist",
  "prompt": "{\"reason\": \"forwarded for expert review\"}",
  "subsession_stream": "AgentSubsession-sess-1-sub-specialist-abc123",
  "timestamp": "2026-05-14T12:00:00Z",
  "extensions": {
    "openai": {
      "raw_item": {
        "type": "function_call",
        "call_id": "call_xyzabc123",
        "name": "transfer_to_specialist",
        "arguments": "{\"reason\": \"forwarded for expert review\"}"
      },
      "item_type": "handoff_call",
      "handoff": {
        "source_agent": "TriageAgent"
      }
    }
  }
}
```

Create `openai-agents/python/tests/fixtures/subagent_completed_openai.json`:

```json
{
  "agent_id": "sub-specialist-abc123",
  "outcome": "success",
  "summary": "Reviewed: approved with notes.",
  "timestamp": "2026-05-14T12:05:00Z",
  "extensions": {
    "openai": {
      "raw_item": {
        "type": "function_call_output",
        "call_id": "call_xyzabc123",
        "output": "Reviewed: approved with notes."
      },
      "item_type": "handoff_output"
    }
  }
}
```

- [ ] **Step 2: Add fixture round-trip tests to test_codec.py**

Append to `openai-agents/python/tests/test_codec.py`:

```python
import json
from pathlib import Path

from kurrent_agent_schema import EVENT_TYPE_BY_NAME, from_json

from kurrent_openai_agents import _serialization

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fixture_name", [
    "subagent_started_openai.json",
    "subagent_completed_openai.json",
])
def test_extensions_openai_fixture_round_trip(fixture_name: str) -> None:
    """OpenAI-flavored lifecycle fixtures preserve extensions.openai shape across serialize."""
    raw = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
    original = json.loads(raw)

    proto_name = "SubagentStarted" if "started" in fixture_name else "SubagentCompleted"
    proto_cls = EVENT_TYPE_BY_NAME[proto_name]
    parsed = from_json(proto_cls, raw)

    serialized = _serialization.serialize(parsed)
    round_tripped = json.loads(serialized.data)

    def _norm(obj):
        if isinstance(obj, dict):
            return {k: _norm(obj[k]) for k in sorted(obj)}
        if isinstance(obj, list):
            return [_norm(x) for x in obj]
        return obj

    assert _norm(round_tripped) == _norm(original)
```

- [ ] **Step 3: Run codec tests**

Run: `cd openai-agents/python && uv run pytest tests/test_codec.py -v -k extensions_openai`
Expected: PASS (2 tests, one per fixture).

- [ ] **Step 4: Run full suite**

Run: `cd openai-agents/python && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/tests/fixtures/ openai-agents/python/tests/test_codec.py
git commit -m "$(cat <<'EOF'
test(openai-agents): extensions.openai fixtures for Subagent* round-trip (AI-471)

Integration-local fixtures exercise the extensions.openai.raw_item and
extensions.openai.handoff.source_agent shape. Shared schema fixtures
(Claude-Code-flavored) keep covering the canonical contract; OpenAI
extension semantics ride here.
EOF
)"
```

---

## Task 12: Sample app — triage-and-specialist handoff demo

**Files:**
- Create: `openai-agents/python/samples/handoff_demo/__init__.py`
- Create: `openai-agents/python/samples/handoff_demo/main.py`
- Create: `openai-agents/python/samples/handoff_demo/README.md`

- [ ] **Step 1: Add the sample**

Create `openai-agents/python/samples/handoff_demo/__init__.py` (empty):

```python
```

Create `openai-agents/python/samples/handoff_demo/main.py`:

```python
"""End-to-end demo of canonical handoffs with ``KurrentDBSession`` (AI-471).

A triage agent receives a vague request and hands off to one of two specialist
agents. The session persists the conversation with:

  - parent ``AgentSession-{id}`` stream: triage's turns + ``SubagentStarted`` /
    ``SubagentCompleted`` lifecycle markers for the specialist invocation.
  - ``AgentSubsession-{id}-{agent_id}`` stream: the specialist's transcript.

Demonstrates the ``session=s, hooks=s`` wiring required to promote handoffs
to canonical events. Without ``hooks=s`` the same code degrades to
``OpenAIItem``-wrapped handoff dicts on the parent stream — useful, but
opaque to cross-framework readers.

Prerequisites:
    - KurrentDB running:    docker compose up -d
    - OpenAI API key:       export OPENAI_API_KEY=sk-...

Run:
    python -m samples.handoff_demo.main           # from openai-agents/python/
"""

from __future__ import annotations

import asyncio
import uuid

from agents import Agent, Runner

from kurrent_openai_agents import KurrentDBSession, client as kdb_client


async def _amain() -> None:
    kdb = kdb_client.from_connection_string("kurrentdb://localhost:2113?Tls=false")
    session_id = f"handoff-demo-{uuid.uuid4().hex[:8]}"
    session = KurrentDBSession(
        session_id=session_id, client=kdb, app_name="handoff-demo", user_id="local",
    )

    weather = Agent(
        name="Weather", instructions="You answer only weather questions concisely.",
    )
    history = Agent(
        name="History", instructions="You answer only history questions concisely.",
    )
    triage = Agent(
        name="Triage",
        instructions=(
            "Route the user to Weather for weather questions and History for "
            "history questions. Use the appropriate handoff."
        ),
        handoffs=[weather, history],
    )

    result = await Runner.run(
        triage, "Was the Battle of Hastings rainy?",
        session=session, hooks=session,
    )
    print(f"Final response from {result.last_agent.name}:")
    print(result.final_output)
    print()
    print(f"Stream layout for session {session_id}:")
    print(f"  parent:     AgentSession-{session_id}")
    print(f"  subagent:   AgentSubsession-{session_id}-sub-<role>-<callid>")
    print()
    print("Replay (flat list as the SDK sees it):")
    for i, item in enumerate(await session.get_items()):
        print(f"  [{i}] type={item.get('type'):20s}")


if __name__ == "__main__":
    asyncio.run(_amain())
```

Create `openai-agents/python/samples/handoff_demo/README.md`:

```markdown
# handoff_demo

Demonstrates canonical handoff promotion via `KurrentDBSession` (AI-471).

A triage agent routes the user to one of two specialists. The session writes:

- `AgentSession-{id}` (parent): triage's turns + `SubagentStarted` / `SubagentCompleted` lifecycle.
- `AgentSubsession-{id}-{agent_id}` (subagent): the specialist's transcript.

Run:

    docker compose up -d                  # KurrentDB
    export OPENAI_API_KEY=sk-...
    python -m samples.handoff_demo.main   # from openai-agents/python/

The `session=s, hooks=s` wiring is required for canonical handoff
promotion — without `hooks=s`, handoff dicts persist as `OpenAIItem`
events on the parent stream (still useful, but opaque to ADK / MAF /
Strands / Capacitor readers).
```

- [ ] **Step 2: Smoke-import the module**

Run: `cd openai-agents/python && uv run python -c "import samples.handoff_demo.main"`
Expected: No output (clean import).

- [ ] **Step 3: Commit**

```bash
git add openai-agents/python/samples/handoff_demo/
git commit -m "$(cat <<'EOF'
docs(openai-agents): handoff_demo sample exercising canonical subagent promotion (AI-471)

Mirrors the basic_agent sample shape with a triage → specialist
handoff. Documents the session=s, hooks=s wiring users need for
canonical promotion vs. the OpenAIItem-fallback degradation path.
EOF
)"
```

---

## Task 13: Update DESIGN.md, README.md, and the monorepo CLAUDE.md row

**Files:**
- Modify: `openai-agents/python/DESIGN.md`
- Modify: `openai-agents/python/README.md`
- Modify: `CLAUDE.md` (repo root)

- [ ] **Step 1: Update `DESIGN.md` §4 handoff catalogue**

In `openai-agents/python/DESIGN.md`, find the §4 entry that reads:

```
- **Handoffs** — `handoff_call` and `handoff_output` items. LLM-driven nested agent invocation. Currently ride as `OpenAIItem`; promotion to canonical `SubagentStarted` / `SubagentCompleted` (with separate `AgentSubsession-` streams) is tracked under DEV-1684.
```

Replace with:

```
- **Handoffs** — `handoff_call` and `handoff_output` items. LLM-driven nested agent invocation. Promoted to canonical `SubagentStarted` / `SubagentCompleted` per SCHEMA_v2 §3.5 when `Runner.run(..., session=s, hooks=s)` is wired (the session itself implements `RunHooksBase`). The subagent's transcript lands on `AgentSubsession-{session_id}-{agent_id}`; the parent and subsession copies of the lifecycle events are written atomically via `multi_append_to_stream`. When hooks are not wired, the items degrade to `OpenAIItem` on the parent stream — useful, but opaque to cross-framework readers. See AI-471.
```

In §3 (write path table), update the `handoff_call` / `handoff_output` rows:

```
| `handoff_call` | `SubagentStarted` (when hooks=session) | else `OpenAIItem`. `agent_id` derived as `sub-{slug(target.name)}-{call_id[-6:]}` per SCHEMA_v2 §2.4. |
| `handoff_output` | `SubagentCompleted` (when hooks=session) | else `OpenAIItem`. `outcome="success"`; `summary` is the truncated tool output. |
```

In §8 Open questions, update Q3 from:

```
3. **Handoff visibility** — handled in DEV-1684. Promotion to canonical `SubagentStarted` / `SubagentCompleted` requires routing the handoff target's items to a separate `AgentSubsession-{parent}-{agent_id}` stream and re-flattening on `get_items`. Out of scope for the schema-v2 cutover.
```

to:

```
3. ~~**Handoff visibility.**~~ ✅ Resolved AI-471. Promotion via `RunHooksBase` on `KurrentDBSession` itself; the parent's `SubagentStarted` carries the original `handoff_call` dict under `extensions.openai.raw_item` so OpenAI's flat-replay invariant is preserved. Subsession streams are atomically dual-written for self-describing reads. Nested handoffs deliberately deferred per the schema's flat-only stance.
```

- [ ] **Step 2: Update `README.md`**

In `openai-agents/python/README.md`, replace the second paragraph (the "alpha" status one) with:

```markdown
**Status: alpha.** `KurrentDBSession` implements both the SDK's `Session` protocol and `RunHooksBase`, so the same object drops into `Runner(..., session=s, hooks=s)` for both persistence and handoff promotion. Items decompose into canonical events on write; handoffs become `SubagentStarted` / `SubagentCompleted` on the parent and a dedicated `AgentSubsession-{id}-{agent_id}` stream. On read, the subsession transcript is re-flattened into the SDK's flat item list with the original `raw_item` preserved under `extensions.openai`.
```

- [ ] **Step 3: Update the monorepo `CLAUDE.md` OpenAI Agents row**

In the repo-root `CLAUDE.md`, find the OpenAI Agents row in the per-integration table. Update the "Storage style" cell to:

```
typed canonical events via shared `kurrent-agent-schema` (Python); decompose items to canonical events (incl. `reasoning` → `AssistantThinkingGenerated`, `mcp_approval_*` → `InterruptIssued`/`InterruptResolved`, `handoff_call` / `handoff_output` → `SubagentStarted` / `SubagentCompleted` with atomic dual-stream emission when `Runner.run(..., session=s, hooks=s)` is wired); full original dict in `extensions.openai.raw_item` for lossless round-trip; non-canonical items wrap as `OpenAIItem`.
```

Also update the "Gotchas" section: remove DEV-1684 reference (it's been promoted) and consider adding a gotcha about `hooks=s` wiring if useful — only add if it's a meaningful trap the next agent would hit.

- [ ] **Step 4: Commit**

```bash
git add openai-agents/python/DESIGN.md openai-agents/python/README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(openai-agents): document canonical handoff promotion (AI-471)

Updates DESIGN.md §3 write-path table, §4 handoff entry, and §8 Q3
deferral note; refreshes README's alpha-status paragraph; updates the
monorepo CLAUDE.md OpenAI Agents row to describe the dual-stream
SubagentStarted/Completed promotion and the session=hooks wiring.
EOF
)"
```

---

## Task 14: Final verification — full suite + a brief manual smoke

**Files:** none modified.

- [ ] **Step 1: Run the full test suite**

Run: `cd openai-agents/python && uv run pytest -q`
Expected: PASS. The suite size should be ~83 (pre-AI-471) + 30+ new tests across handoffs, serialization, codec extension fixtures, and integration cases.

- [ ] **Step 2: Confirm no untracked files were forgotten**

Run: `git status`
Expected: clean working tree (everything committed).

- [ ] **Step 3: Inspect the commit log to confirm task structure**

Run: `git log --oneline -15`
Expected: 13 commits (one per task except Tasks 3+4 share one, and Task 14 has no commit) — clean, atomic, each task represented.

- [ ] **Step 4: Run a quick manual smoke against a real KurrentDB**

```bash
cd openai-agents/python
docker compose up -d
export OPENAI_API_KEY=sk-...   # required for the LLM call in the sample
uv run python -m samples.handoff_demo.main
```

Expected output: the script prints the final response, the stream layout, and a flat-replay listing. The KurrentDB UI (typically `http://localhost:2113`) should show two streams: `AgentSession-handoff-demo-{...}` and one `AgentSubsession-handoff-demo-{...}-sub-{...}`.

- [ ] **Step 5: (No commit — this task is verification only.)**

If everything passes, the PR is ready. Push the branch and open a PR titled "feat(openai-agents): promote handoffs to canonical subagent events (AI-471)" referencing the spec at `docs/superpowers/specs/2026-05-13-openai-agents-handoff-promotion-design.md` and the related Linear issues AI-471 and AI-621.
