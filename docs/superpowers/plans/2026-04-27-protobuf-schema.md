# Protobuf canonical schema migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hand-maintained `kurrent-agent-schema` (Python) and `Kurrent.Agent.Schema` (.NET) packages with codegen from a single Protobuf source, keeping the JSON-on-the-wire format and the public package API.

**Architecture:** Protobuf files at `schema/proto/kurrent/agent/v2/` are the source of truth. `buf` generates language-specific message classes into committed `_generated/` (Python) and `Generated/` (.NET) directories. Each package adds a hand-written sibling module providing the JSON entry-point helper, stream-name builders, type registry, and version constant. The published API surface (class names, module paths) is unchanged so downstream integrations re-import without code changes. JSON wire format uses proto3 JSON canonical form with `preserve_proto_field_name` enabled.

**Tech Stack:** Protocol Buffers proto3, [buf](https://buf.build/) CLI, Python 3.11+ with `protobuf` package, .NET 10 with `Google.Protobuf`, existing `uv` and `dotnet` toolchains.

**Reference docs:**
- Spec: `docs/superpowers/specs/2026-04-27-protobuf-schema-design.md`
- Schema doc: `schema/SCHEMA_v2.md`
- Existing packages: `schema/python/kurrent_agent_schema/`, `schema/dotnet/Kurrent.Agent.Schema/`

---

## File Structure

**Created:**
```
schema/buf.yaml
schema/buf.gen.yaml
schema/proto/kurrent/agent/v2/value_types.proto
schema/proto/kurrent/agent/v2/usage.proto
schema/proto/kurrent/agent/v2/events.proto
schema/python/kurrent_agent_schema/_generated/        (committed buf output)
schema/python/kurrent_agent_schema/json.py            (sanctioned JSON helper)
schema/python/kurrent_agent_schema/registry.py        (event-type registry)
schema/dotnet/Kurrent.Agent.Schema/Generated/         (committed buf output)
schema/dotnet/Kurrent.Agent.Schema.Tests/JsonFormatterTests.cs
schema/dotnet/Kurrent.Agent.Schema.Tests/DumpCanonicalOutput.cs
schema/python/tests/test_json_helper.py
schema/python/tests/dump_canonical_output.py
schema/Makefile
schema/python/CHANGELOG.md
schema/dotnet/Kurrent.Agent.Schema/CHANGELOG.md
.github/workflows/schema-codegen.yml
.github/workflows/schema-cross-language.yml
```

**Modified:**
```
schema/python/kurrent_agent_schema/__init__.py        (re-export generated types)
schema/python/kurrent_agent_schema/events.py          (deleted: replaced by _generated + registry)
schema/python/kurrent_agent_schema/usage.py           (kept; minor updates if needed)
schema/python/kurrent_agent_schema/streams.py         (unchanged)
schema/python/kurrent_agent_schema/version.py         (bumped)
schema/python/pyproject.toml                          (version + protobuf dep)
schema/python/tests/test_fixtures.py                  (use new helper API)
schema/dotnet/Kurrent.Agent.Schema/Events/*.cs        (deleted: replaced by Generated/)
schema/dotnet/Kurrent.Agent.Schema/SchemaJsonOptions.cs   (rewritten on top of JsonFormatter)
schema/dotnet/Kurrent.Agent.Schema/EventTypeMap.cs    (point at generated types)
schema/dotnet/Kurrent.Agent.Schema/TokenUsage.cs      (replaced by generated)
schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj   (version + Google.Protobuf dep + buf integration)
schema/dotnet/Kurrent.Agent.Schema.Tests/FixtureRoundTripTests.cs   (use new helper API)
schema/fixtures/events/*.json                         (regenerated once)
schema/fixtures/metadata/usage.json                   (regenerated once)
.github/workflows/schema-ci-python.yml                (add buf path triggers)
.github/workflows/schema-ci-dotnet.yml                (add buf path triggers)
```

**Deleted at the end:**
```
schema/python/kurrent_agent_schema/events.py
schema/dotnet/Kurrent.Agent.Schema/Events/
schema/dotnet/Kurrent.Agent.Schema/TokenUsage.cs
schema/dotnet/Kurrent.Agent.Schema/IsoDateTimeOffsetConverter.cs   (if separated; currently inside SchemaJsonOptions.cs)
```

---

## Task 1: Set up `buf` toolchain and skeleton config

**Files:**
- Create: `schema/buf.yaml`
- Create: `schema/buf.gen.yaml`
- Modify: `.config/dotnet-tools.json` (root) — register no tool here; buf is a Go binary, install via Homebrew/script
- Create: `schema/Makefile` — single entry point for `make schema`

**Rationale:** `buf` is the codegen driver. Both languages' generators run from `buf.gen.yaml`. Skeleton lands first so subsequent tasks have a working `buf generate` to run against.

- [ ] **Step 1: Verify `buf` is available locally**

```bash
buf --version
```

Expected: prints a version string (e.g. `1.50.0`). If missing, install: `brew install bufbuild/buf/buf`.

- [ ] **Step 2: Write `schema/buf.yaml`**

```yaml
version: v2
modules:
  - path: proto
lint:
  use:
    - STANDARD
  except:
    # Intentional: TokenUsage (csharp_namespace = "Kurrent.Agent.Schema")
    # and events/value types (csharp_namespace = "Kurrent.Agent.Schema.Events")
    # share proto package "kurrent.agent.v2" but live in different .NET
    # namespaces to preserve the existing hand-written package layout.
    # All other STANDARD rules remain in force.
    - PACKAGE_SAME_CSHARP_NAMESPACE
breaking:
  use:
    - WIRE_JSON
```

- [ ] **Step 3: Write `schema/buf.gen.yaml`**

```yaml
version: v2
plugins:
  - remote: buf.build/protocolbuffers/python
    out: python/kurrent_agent_schema/_generated
  - remote: buf.build/protocolbuffers/pyi
    out: python/kurrent_agent_schema/_generated
  - remote: buf.build/protocolbuffers/csharp
    out: dotnet/Kurrent.Agent.Schema/Generated
inputs:
  - directory: proto
```

- [ ] **Step 4: Write `schema/Makefile`**

```makefile
.PHONY: schema lint breaking

schema:
	buf generate

lint:
	buf lint

breaking:
	buf breaking --against '.git#branch=main,subdir=schema'
```

- [ ] **Step 5: Verify config validates (no protos yet, so nothing to generate)**

```bash
cd schema && buf lint
```

Expected: no output (zero proto files to lint, no errors).

- [ ] **Step 6: Commit**

```bash
git add schema/buf.yaml schema/buf.gen.yaml schema/Makefile
git commit -m "feat(schema): add buf toolchain skeleton"
```

---

## Task 2: Author `value_types.proto`

**Files:**
- Create: `schema/proto/kurrent/agent/v2/value_types.proto`

**Rationale:** Value types (`ToolSpec`, `AgentConfig`, `ToolCallInfo`) are referenced by `events.proto`, so they must be defined first. They have no inter-dependencies with events.

- [ ] **Step 1: Write the proto file**

`schema/proto/kurrent/agent/v2/value_types.proto`:

```proto
syntax = "proto3";

package kurrent.agent.v2;

import "google/protobuf/struct.proto";

option csharp_namespace = "Kurrent.Agent.Schema.Events";

// Tool description captured in AgentConfig.tools.
message ToolSpec {
  string name = 1;
  optional string description = 2;
  // Free-form JSON schema describing the tool inputs.
  // Absent (null in JSON) when not provided.
  google.protobuf.Struct input_schema = 3;
  optional string source = 4;
}

// Informational snapshot of the agent configuration at session start.
message AgentConfig {
  repeated ToolSpec tools = 1;
  repeated string plugins = 2;
  // Free-form JSON description of the conversation manager.
  google.protobuf.Struct conversation_manager = 3;
  // Free-form JSON description of model parameters (temperature, etc).
  google.protobuf.Struct model_parameters = 4;
}

// One tool call within AssistantToolCallsGenerated.tool_calls.
message ToolCallInfo {
  string call_id = 1;
  string tool_name = 2;
  // Free-form JSON arguments. Empty object preserved (Pydantic codec
  // empty-args bug — see commit ff1540d).
  google.protobuf.Struct arguments = 3;
}
```

- [ ] **Step 2: Lint**

```bash
cd schema && buf lint
```

Expected: no output (clean lint).

- [ ] **Step 3: Commit**

```bash
git add schema/proto/kurrent/agent/v2/value_types.proto
git commit -m "feat(schema): add value_types.proto (ToolSpec, AgentConfig, ToolCallInfo)"
```

---

## Task 3: Author `usage.proto`

**Files:**
- Create: `schema/proto/kurrent/agent/v2/usage.proto`

**Rationale:** `TokenUsage` lives on KurrentDB event metadata under `$usage`, not in payloads. Independent of events.proto.

- [ ] **Step 1: Write the proto file**

`schema/proto/kurrent/agent/v2/usage.proto`:

```proto
syntax = "proto3";

package kurrent.agent.v2;

import "google/protobuf/struct.proto";

// Different csharp_namespace from value_types.proto / events.proto is
// intentional and is granted by the PACKAGE_SAME_CSHARP_NAMESPACE
// exception in schema/buf.yaml. TokenUsage stays at the existing
// Kurrent.Agent.Schema root namespace; events stay under .Events.
option csharp_namespace = "Kurrent.Agent.Schema";

// Token-usage record placed on KurrentDB event metadata under the $usage key.
// See SCHEMA_v2.md §3.6.
message TokenUsage {
  optional int64 input_tokens = 1;
  optional int64 output_tokens = 2;
  optional int64 total_tokens = 3;
  optional int64 cached_input_tokens = 4;
  optional int64 reasoning_tokens = 5;
  optional string model = 6;
  // Free-form bucket for provider-specific counters not mapped to canonical
  // slots. Values may themselves be objects (e.g. nested ServerToolUse).
  // Modelled as Struct (not map<string, int64>) to admit nested values.
  google.protobuf.Struct additional_counts = 7;
}
```

- [ ] **Step 2: Lint**

```bash
cd schema && buf lint
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add schema/proto/kurrent/agent/v2/usage.proto
git commit -m "feat(schema): add usage.proto (TokenUsage)"
```

---

## Task 4: Author `events.proto`

**Files:**
- Create: `schema/proto/kurrent/agent/v2/events.proto`

**Rationale:** Single file holding all 17 canonical event types. Imports `value_types.proto` and standard well-known types.

- [ ] **Step 1: Write the proto file**

`schema/proto/kurrent/agent/v2/events.proto`:

```proto
syntax = "proto3";

package kurrent.agent.v2;

import "google/protobuf/struct.proto";
import "google/protobuf/timestamp.proto";

import "kurrent/agent/v2/value_types.proto";

option csharp_namespace = "Kurrent.Agent.Schema.Events";

// =====================================================================
// Session lifecycle (SCHEMA_v2.md §3.1)
// =====================================================================

message SessionStarted {
  optional string app_name = 1;
  optional string agent_name = 2;
  optional string model = 3;
  optional string tenant_id = 4;
  optional string user_id = 5;
  AgentConfig agent_config = 6;
  optional string previous_session_id = 7;
  google.protobuf.Timestamp timestamp = 8;
  // Framework-specific extension envelope keyed by slug.
  // See SCHEMA_v2.md §5.
  map<string, google.protobuf.Struct> extensions = 9;
}

message SessionEnded {
  optional string reason = 1;
  google.protobuf.Timestamp timestamp = 2;
  map<string, google.protobuf.Struct> extensions = 3;
}

message SessionContinuedAs {
  string next_session_id = 1;
  optional string reason = 2;
  google.protobuf.Timestamp timestamp = 3;
  map<string, google.protobuf.Struct> extensions = 4;
}

// =====================================================================
// Conversation events (SCHEMA_v2.md §3.4)
// =====================================================================

message UserMessageReceived {
  optional string content = 1;
  optional string message_id = 2;
  optional string author_name = 3;
  optional google.protobuf.Timestamp created_at = 4;
  int32 message_index = 5;
  google.protobuf.Timestamp timestamp = 6;
  map<string, google.protobuf.Struct> extensions = 7;
}

message AssistantTextGenerated {
  optional string content = 1;
  optional string message_id = 2;
  optional string author_name = 3;
  optional google.protobuf.Timestamp created_at = 4;
  int32 message_index = 5;
  google.protobuf.Timestamp timestamp = 6;
  map<string, google.protobuf.Struct> extensions = 7;
}

message AssistantToolCallsGenerated {
  repeated ToolCallInfo tool_calls = 1;
  optional string content = 2;
  optional string message_id = 3;
  optional string author_name = 4;
  optional google.protobuf.Timestamp created_at = 5;
  int32 message_index = 6;
  google.protobuf.Timestamp timestamp = 7;
  map<string, google.protobuf.Struct> extensions = 8;
}

message AssistantThinkingGenerated {
  optional string content = 1;
  // Provider returned an opaque blob instead of plaintext (OpenAI o-series).
  // The opaque value, when present, lives under extensions.openai.thinking.raw.
  bool encrypted = 2;
  optional string signature = 3;
  optional string message_id = 4;
  optional string author_name = 5;
  optional google.protobuf.Timestamp created_at = 6;
  int32 message_index = 7;
  google.protobuf.Timestamp timestamp = 8;
  map<string, google.protobuf.Struct> extensions = 9;
}

message ToolResultReceived {
  string call_id = 1;
  optional string tool_name = 2;
  optional string result = 3;
  optional string message_id = 4;
  optional string author_name = 5;
  optional google.protobuf.Timestamp created_at = 6;
  int32 message_index = 7;
  google.protobuf.Timestamp timestamp = 8;
  map<string, google.protobuf.Struct> extensions = 9;
}

// =====================================================================
// Interrupts (SCHEMA_v2.md §3.3)
// =====================================================================

message InterruptIssued {
  string request_id = 1;
  // Open string. Documented set: permission | approval | input | auth.
  string kind = 2;
  optional string tool_name = 3;
  optional string prompt = 4;
  optional string message_id = 5;
  google.protobuf.Timestamp timestamp = 6;
  map<string, google.protobuf.Struct> extensions = 7;
}

message InterruptResolved {
  string request_id = 1;
  // Open string. Documented set: allow | allow_once | allow_always |
  // deny | cancel | answered | timeout.
  string outcome = 2;
  optional string response = 3;
  optional string message_id = 4;
  google.protobuf.Timestamp timestamp = 5;
  map<string, google.protobuf.Struct> extensions = 6;
}

// =====================================================================
// Subagents (SCHEMA_v2.md §3.5)
// =====================================================================

message SubagentStarted {
  string agent_id = 1;
  optional string agent_type = 2;
  optional string prompt = 3;
  optional string subsession_stream = 4;
  google.protobuf.Timestamp timestamp = 5;
  map<string, google.protobuf.Struct> extensions = 6;
}

message SubagentCompleted {
  string agent_id = 1;
  optional string outcome = 2;
  optional string summary = 3;
  google.protobuf.Timestamp timestamp = 4;
  map<string, google.protobuf.Struct> extensions = 5;
}

// =====================================================================
// Memory (SCHEMA_v2.md §3.7)
// =====================================================================

message FactRetained {
  string fact = 1;
  google.protobuf.Timestamp retained_at = 2;
  map<string, google.protobuf.Struct> extensions = 3;
}

// =====================================================================
// Artifacts (SCHEMA_v2.md §3.7)
// =====================================================================

message ArtifactVersionCreated {
  int32 version = 1;
  optional string mime_type = 2;
  optional bytes inline_bytes = 3;  // base64 in JSON; absent when null
  optional string canonical_uri = 4;
  google.protobuf.Struct custom_metadata = 5;
  google.protobuf.Timestamp created_at = 6;
  map<string, google.protobuf.Struct> extensions = 7;
}

// =====================================================================
// Evaluation (SCHEMA_v2.md §3.7)
// =====================================================================

message EvalRunStarted {
  string session_id = 1;
  string scorer = 2;
  string criteria = 3;
  google.protobuf.Timestamp timestamp = 4;
  map<string, google.protobuf.Struct> extensions = 5;
}

message TurnScored {
  string session_id = 1;
  int32 turn_index = 2;
  optional string input = 3;
  optional string output = 4;
  double score = 5;
  optional string score_label = 6;
  optional string reason = 7;
  google.protobuf.Timestamp timestamp = 8;
  map<string, google.protobuf.Struct> extensions = 9;
}

message EvalRunCompleted {
  string session_id = 1;
  int32 turns_scored = 2;
  double average_score = 3;
  optional double total_cost = 4;
  google.protobuf.Timestamp timestamp = 5;
  map<string, google.protobuf.Struct> extensions = 6;
}
```

- [ ] **Step 2: Lint**

```bash
cd schema && buf lint
```

Expected: clean.

- [ ] **Step 3: Verify against existing schema**

Open `schema/SCHEMA_v2.md §3` and `schema/python/kurrent_agent_schema/events.py` side-by-side. Confirm: every event type, every field, with matching nullability and types. Particularly check `inline_bytes` (bytes), `score`/`average_score`/`total_cost` (double), `message_index`/`turn_index`/`turns_scored`/`version` (int32), `additional_counts` location (it's on TokenUsage in usage.proto, not on events).

- [ ] **Step 4: Commit**

```bash
git add schema/proto/kurrent/agent/v2/events.proto
git commit -m "feat(schema): add events.proto (17 canonical event types)"
```

---

## Task 5: Generate Python code and commit the `_generated/` tree

**Files:**
- Modify: `schema/python/pyproject.toml` (add `protobuf` runtime dep)
- Create: `schema/python/kurrent_agent_schema/_generated/` (committed buf output, including `__init__.py` files)

**Rationale:** First codegen run. The generated tree is committed so downstream consumers don't need protoc. Adding `__init__.py` files turns the directory into a Python package.

- [ ] **Step 1: Add `protobuf` to `pyproject.toml`**

Modify `schema/python/pyproject.toml` to add `protobuf` alongside the existing `pydantic` dep:

```toml
dependencies = [
  "pydantic >= 2.5",
  "protobuf >= 5.27, < 7",
]
```

`pydantic` cannot be dropped here because `events.py` still imports it; that drop happens in Task 7 when `events.py` is deleted.

- [ ] **Step 2: Run codegen**

```bash
cd schema && buf generate
```

Expected: produces files under `schema/python/kurrent_agent_schema/_generated/kurrent/agent/v2/`:
- `value_types_pb2.py`, `value_types_pb2.pyi`
- `usage_pb2.py`, `usage_pb2.pyi`
- `events_pb2.py`, `events_pb2.pyi`

- [ ] **Step 3: Add `__init__.py` files to make `_generated` a Python package**

Create `schema/python/kurrent_agent_schema/_generated/__init__.py` (empty file).
Create `schema/python/kurrent_agent_schema/_generated/kurrent/__init__.py` (empty file).
Create `schema/python/kurrent_agent_schema/_generated/kurrent/agent/__init__.py` (empty file).
Create `schema/python/kurrent_agent_schema/_generated/kurrent/agent/v2/__init__.py` (empty file).

- [ ] **Step 4: Sync deps and verify imports**

```bash
cd schema/python
uv sync --all-extras
uv run python -c "from kurrent_agent_schema._generated.kurrent.agent.v2 import events_pb2; print(events_pb2.SessionStarted.DESCRIPTOR.full_name)"
```

Expected: `kurrent.agent.v2.SessionStarted`.

- [ ] **Step 5: Commit**

```bash
git add schema/python/pyproject.toml schema/python/uv.lock schema/python/kurrent_agent_schema/_generated/
git commit -m "feat(schema): generate Python protobuf code from buf"
```

---

## Task 6: Write the Python `json.py` and `registry.py` helpers

**Files:**
- Create: `schema/python/kurrent_agent_schema/json.py`
- Create: `schema/python/kurrent_agent_schema/registry.py`

**Rationale:** `json.py` is the single sanctioned JSON entry point — bakes in `preserving_proto_field_name=True`. `registry.py` is the event-type-name ↔ class registry.

- [ ] **Step 1: Write a failing test for `to_json` / `from_json`**

Append to `schema/python/tests/test_fixtures.py` (or create a new file `tests/test_json_helper.py`):

```python
from kurrent_agent_schema._generated.kurrent.agent.v2 import events_pb2
from kurrent_agent_schema.json import to_json, from_json


def test_to_json_uses_snake_case() -> None:
    msg = events_pb2.SessionStarted(app_name="my-app")
    js = to_json(msg)
    assert '"app_name"' in js
    assert '"appName"' not in js


def test_from_json_round_trip() -> None:
    src = '{"app_name": "x", "timestamp": "2026-01-01T00:00:00Z"}'
    msg = from_json(events_pb2.SessionStarted, src)
    assert msg.app_name == "x"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd schema/python
uv run pytest tests/test_json_helper.py -v
```

Expected: ImportError on `kurrent_agent_schema.json`.

- [ ] **Step 3: Write `json.py`**

`schema/python/kurrent_agent_schema/json.py`:

```python
"""Sanctioned JSON entry point for canonical events.

Direct calls to ``google.protobuf.json_format`` are forbidden outside this
module. This is the only place the ``preserving_proto_field_name=True``
flag is set, so the snake_case wire format cannot be drifted by accident.
"""

from __future__ import annotations

from typing import TypeVar

from google.protobuf import json_format
from google.protobuf.message import Message

T = TypeVar("T", bound=Message)


def to_json(message: Message) -> str:
    """Serialise a canonical event to its JSON wire form (snake_case keys)."""
    return json_format.MessageToJson(
        message,
        preserving_proto_field_name=True,
        including_default_value_fields=False,
        sort_keys=False,
        indent=None,
    )


def from_json(message_type: type[T], src: str) -> T:
    """Parse a JSON string into the given canonical event message type."""
    msg = message_type()
    json_format.Parse(src, msg, ignore_unknown_fields=True)
    return msg
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd schema/python
uv run pytest tests/test_json_helper.py -v
```

Expected: PASS.

- [ ] **Step 5: Write `registry.py`**

`schema/python/kurrent_agent_schema/registry.py`:

```python
"""Event-type-name ↔ generated message class registry.

Stamps the KurrentDB ``event_type`` for each canonical event class.
"""

from __future__ import annotations

from google.protobuf.message import Message

from kurrent_agent_schema._generated.kurrent.agent.v2 import events_pb2 as _ev

EVENT_TYPE_NAMES: dict[type[Message], str] = {
    _ev.SessionStarted: "SessionStarted",
    _ev.SessionEnded: "SessionEnded",
    _ev.SessionContinuedAs: "SessionContinuedAs",
    _ev.UserMessageReceived: "UserMessageReceived",
    _ev.AssistantTextGenerated: "AssistantTextGenerated",
    _ev.AssistantToolCallsGenerated: "AssistantToolCallsGenerated",
    _ev.AssistantThinkingGenerated: "AssistantThinkingGenerated",
    _ev.ToolResultReceived: "ToolResultReceived",
    _ev.InterruptIssued: "InterruptIssued",
    _ev.InterruptResolved: "InterruptResolved",
    _ev.SubagentStarted: "SubagentStarted",
    _ev.SubagentCompleted: "SubagentCompleted",
    _ev.FactRetained: "FactRetained",
    _ev.ArtifactVersionCreated: "ArtifactVersionCreated",
    _ev.EvalRunStarted: "EvalRunStarted",
    _ev.TurnScored: "TurnScored",
    _ev.EvalRunCompleted: "EvalRunCompleted",
}

EVENT_TYPE_BY_NAME: dict[str, type[Message]] = {v: k for k, v in EVENT_TYPE_NAMES.items()}
```

- [ ] **Step 6: Add a registry sanity test**

Append to `tests/test_json_helper.py`:

```python
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME


def test_registry_contains_all_canonical_events() -> None:
    expected = {
        "SessionStarted", "SessionEnded", "SessionContinuedAs",
        "UserMessageReceived", "AssistantTextGenerated",
        "AssistantToolCallsGenerated", "AssistantThinkingGenerated",
        "ToolResultReceived", "InterruptIssued", "InterruptResolved",
        "SubagentStarted", "SubagentCompleted", "FactRetained",
        "ArtifactVersionCreated", "EvalRunStarted", "TurnScored",
        "EvalRunCompleted",
    }
    assert set(EVENT_TYPE_BY_NAME) == expected
```

- [ ] **Step 7: Run all tests in this module**

```bash
cd schema/python
uv run pytest tests/test_json_helper.py -v
```

Expected: 3 PASS.

- [ ] **Step 8: Commit**

```bash
git add schema/python/kurrent_agent_schema/json.py \
        schema/python/kurrent_agent_schema/registry.py \
        schema/python/tests/test_json_helper.py
git commit -m "feat(schema-py): json.py + registry.py helpers atop generated types"
```

---

## Task 7: Switch Python public API to generated types and remove `events.py`

**Files:**
- Modify: `schema/python/kurrent_agent_schema/__init__.py`
- Delete: `schema/python/kurrent_agent_schema/events.py`
- Modify: `schema/python/kurrent_agent_schema/usage.py`
- Modify: `schema/python/tests/test_fixtures.py`
- Modify: `schema/fixtures/events/*.json` (regenerated once)
- Modify: `schema/fixtures/metadata/usage.json` (regenerated once)

**Rationale:** Replace the Pydantic-backed public API with re-exports of generated message classes. Existing consumers `from kurrent_agent_schema import SessionStarted` keep working, the import now resolves to a `Message` subclass instead of a Pydantic model. Fixtures regenerate to match proto3 JSON canonical form (no explicit `null` for unset optionals).

- [ ] **Step 1: Rewrite `__init__.py`**

`schema/python/kurrent_agent_schema/__init__.py`:

```python
"""Canonical event schema for Kurrent agent integrations.

See ``schema/SCHEMA_v2.md`` at the repo root for the prose specification.
"""

from kurrent_agent_schema._generated.kurrent.agent.v2.events_pb2 import (
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
    ToolResultReceived,
    UserMessageReceived,
)
from kurrent_agent_schema._generated.kurrent.agent.v2.usage_pb2 import TokenUsage
from kurrent_agent_schema._generated.kurrent.agent.v2.value_types_pb2 import (
    AgentConfig,
    ToolCallInfo,
    ToolSpec,
)
from kurrent_agent_schema.json import from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME, EVENT_TYPE_NAMES
from kurrent_agent_schema.streams import (
    agent_artifact_stream,
    agent_memory_stream,
    agent_session_stream,
    agent_subsession_stream,
    eval_run_stream,
)
from kurrent_agent_schema.usage import USAGE_METADATA_KEY
from kurrent_agent_schema.version import SCHEMA_VERSION

__all__ = [
    # Value types
    "AgentConfig", "ToolSpec", "ToolCallInfo", "TokenUsage",
    # Session lifecycle
    "SessionStarted", "SessionEnded", "SessionContinuedAs",
    # Conversation
    "UserMessageReceived", "AssistantTextGenerated",
    "AssistantToolCallsGenerated", "AssistantThinkingGenerated",
    "ToolResultReceived",
    # Interrupts
    "InterruptIssued", "InterruptResolved",
    # Subagents
    "SubagentStarted", "SubagentCompleted",
    # Memory / artifacts / eval
    "FactRetained", "ArtifactVersionCreated",
    "EvalRunStarted", "TurnScored", "EvalRunCompleted",
    # Stream builders
    "agent_session_stream", "agent_subsession_stream",
    "agent_memory_stream", "agent_artifact_stream", "eval_run_stream",
    # Helpers
    "to_json", "from_json",
    # Registry
    "EVENT_TYPE_NAMES", "EVENT_TYPE_BY_NAME",
    # Constants
    "USAGE_METADATA_KEY", "SCHEMA_VERSION",
]
```

- [ ] **Step 2: Delete `events.py` and drop `pydantic` from `pyproject.toml`**

```bash
rm schema/python/kurrent_agent_schema/events.py
```

In `schema/python/pyproject.toml`, remove the `pydantic` dependency line — nothing in the package needs it any more (generated types are pure protobuf, `streams.py`/`usage.py`/`version.py` are stdlib only). The remaining dep is just `protobuf >= 5.27, < 7`.

- [ ] **Step 3: Trim `usage.py` to constants only**

Edit `schema/python/kurrent_agent_schema/usage.py` so it only declares `USAGE_METADATA_KEY = "$usage"` and removes any Pydantic `TokenUsage` definition (now imported from generated). Confirm by reading the file.

- [ ] **Step 4: Rewrite `tests/test_fixtures.py` to use the helper API**

`schema/python/tests/test_fixtures.py`:

```python
"""Drift-detection tests for the Python canonical-types package.

For every fixture under ``schema/fixtures/events/``, parse the JSON via the
sanctioned helper, reserialise, and assert structural equality. The matching
.NET package runs an equivalent test against the same fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kurrent_agent_schema import TokenUsage, from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS_DIR = FIXTURES_ROOT / "events"
METADATA_DIR = FIXTURES_ROOT / "metadata"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _normalise(obj):
    if isinstance(obj, dict):
        return {k: _normalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_normalise(x) for x in obj]
    return obj


def _event_fixture_cases():
    if not EVENTS_DIR.exists():
        return []
    return sorted(EVENTS_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _event_fixture_cases(), ids=lambda p: p.stem)
def test_event_fixture_round_trip(fixture_path: Path) -> None:
    event_name = fixture_path.stem
    cls = EVENT_TYPE_BY_NAME.get(event_name)
    assert cls is not None, f"No canonical model registered for '{event_name}'"

    original = _load(fixture_path)
    parsed = from_json(cls, fixture_path.read_text())
    serialised = json.loads(to_json(parsed))

    assert _normalise(serialised) == _normalise(original), (
        f"Round-trip drift for {event_name}: serialised form does not match fixture"
    )


def test_every_canonical_event_has_a_fixture() -> None:
    missing = [
        name for name in EVENT_TYPE_BY_NAME
        if not (EVENTS_DIR / f"{name}.json").exists()
    ]
    assert not missing, f"Missing fixtures for canonical events: {missing}"


def test_usage_metadata_round_trip() -> None:
    fixture_path = METADATA_DIR / "usage.json"
    original = _load(fixture_path)
    parsed = from_json(TokenUsage, fixture_path.read_text())
    serialised = json.loads(to_json(parsed))
    assert _normalise(serialised) == _normalise(original)
```

- [ ] **Step 5: Run tests; expect failures from null-shape mismatch**

```bash
cd schema/python
uv run pytest -v
```

Expected: many fixture round-trip tests fail because existing fixtures may have explicit `"x": null` keys for some optional fields. The proto3 JSON canonical writer omits these.

- [ ] **Step 6: Regenerate fixtures from the new writer**

For each failing fixture, regenerate from the new writer. Script (`schema/python/tests/regen_fixtures.py`):

```python
"""One-shot fixture regeneration for the proto3 JSON cutover.

Reads each fixture, parses, re-serialises via to_json(), writes back.
Run once after the cutover, then committed alongside the migration.
"""

from __future__ import annotations

import json
from pathlib import Path

from kurrent_agent_schema import TokenUsage, from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME

ROOT = Path(__file__).resolve().parents[2] / "fixtures"

for path in sorted((ROOT / "events").glob("*.json")):
    cls = EVENT_TYPE_BY_NAME[path.stem]
    parsed = from_json(cls, path.read_text())
    new = json.dumps(json.loads(to_json(parsed)), indent=2) + "\n"
    path.write_text(new)
    print(f"Regenerated {path.name}")

usage_path = ROOT / "metadata" / "usage.json"
parsed = from_json(TokenUsage, usage_path.read_text())
new = json.dumps(json.loads(to_json(parsed)), indent=2) + "\n"
usage_path.write_text(new)
print(f"Regenerated metadata/usage.json")
```

Run:

```bash
cd schema/python
uv run python tests/regen_fixtures.py
```

Inspect `git diff schema/fixtures/` — every change should be a `null` value disappearing or whitespace formatting; no semantic drift.

- [ ] **Step 7: Run tests again to confirm pass**

```bash
cd schema/python
uv run pytest -v
```

Expected: all tests PASS.

- [ ] **Step 8: Delete the regen script**

```bash
rm schema/python/tests/regen_fixtures.py
```

(Don't keep it — fixtures are now authoritative; regen would be a one-off again only if proto changed shape.)

- [ ] **Step 9: Commit**

```bash
git add schema/python/kurrent_agent_schema/__init__.py \
        schema/python/kurrent_agent_schema/usage.py \
        schema/python/tests/test_fixtures.py \
        schema/fixtures/
git rm schema/python/kurrent_agent_schema/events.py
git commit -m "refactor(schema-py): swap Pydantic models for generated protobuf types

Public API surface unchanged. Fixtures regenerated once to match proto3
JSON canonical form (unset optional fields are omitted instead of \`null\`)."
```

---

## Task 8: Generate .NET code and wire it into the project

**Files:**
- Modify: `schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj`
- Create: `schema/dotnet/Kurrent.Agent.Schema/Generated/` (committed buf output)

**Rationale:** Add the `Google.Protobuf` package, point the csproj at the generated tree, and confirm a build.

- [ ] **Step 1: Add `Google.Protobuf` to the csproj**

Modify `schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj`:

```xml
<ItemGroup>
  <PackageReference Include="Google.Protobuf" Version="3.28.3" />
</ItemGroup>
```

- [ ] **Step 2: Run codegen**

```bash
cd schema && buf generate
```

Expected: produces `schema/dotnet/Kurrent.Agent.Schema/Generated/Events.cs`, `Usage.cs`, `ValueTypes.cs`. Generated event/value types use `namespace Kurrent.Agent.Schema.Events` (matches the existing hand-written namespace, so consumers' `using` lines don't change). `TokenUsage` is generated under `namespace Kurrent.Agent.Schema`.

- [ ] **Step 3: Build**

```bash
cd schema/dotnet
dotnet build Kurrent.Agent.Schema.slnx -c Release
```

Expected: build succeeds. (Tests will fail at this stage — that's Task 9–10.)

- [ ] **Step 4: Commit**

```bash
git add schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj \
        schema/dotnet/Kurrent.Agent.Schema/Generated/
git commit -m "feat(schema-net): generate C# protobuf code from buf"
```

---

## Task 9: Rewrite `SchemaJsonOptions.cs` on top of `JsonFormatter`

**Files:**
- Modify: `schema/dotnet/Kurrent.Agent.Schema/SchemaJsonOptions.cs`

**Rationale:** Replace the `System.Text.Json` configuration with the protobuf `JsonFormatter` / `JsonParser` configured for `preserveProtoFieldNames`. This is the .NET sanctioned JSON entry point.

- [ ] **Step 1: Write a failing test**

Append to `schema/dotnet/Kurrent.Agent.Schema.Tests/FixtureRoundTripTests.cs` (above the existing class) — or create a new file `JsonFormatterTests.cs`:

```csharp
using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

public class JsonFormatterTests {
    [Fact]
    public void ToJson_uses_snake_case() {
        var msg = new SessionStarted { AppName = "my-app" };
        var json = SchemaJsonOptions.ToJson(msg);
        Assert.Contains("\"app_name\"", json);
        Assert.DoesNotContain("\"appName\"", json);
    }

    [Fact]
    public void FromJson_round_trips() {
        const string src = "{\"app_name\":\"x\",\"timestamp\":\"2026-01-01T00:00:00Z\"}";
        var msg = SchemaJsonOptions.FromJson<SessionStarted>(src);
        Assert.Equal("x", msg.AppName);
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd schema/dotnet
dotnet test Kurrent.Agent.Schema.slnx --filter "FullyQualifiedName~JsonFormatterTests"
```

Expected: compile error — `SchemaJsonOptions.ToJson` doesn't exist.

- [ ] **Step 3: Replace `SchemaJsonOptions.cs`**

`schema/dotnet/Kurrent.Agent.Schema/SchemaJsonOptions.cs`:

```csharp
using Google.Protobuf;

namespace Kurrent.Agent.Schema;

/// <summary>
/// Sanctioned JSON entry point for canonical events. Direct calls to
/// <see cref="JsonFormatter"/> / <see cref="JsonParser"/> are forbidden
/// outside this class — the snake_case wire format depends on the
/// <c>preserveProtoFieldNames=true</c> flag set here.
/// </summary>
public static class SchemaJsonOptions {
    static readonly JsonFormatter Formatter = new(
        new JsonFormatter.Settings(formatDefaultValues: false)
            .WithPreserveProtoFieldNames(true)
            .WithFormatEnumsAsIntegers(false));

    static readonly JsonParser Parser = new(
        JsonParser.Settings.Default.WithIgnoreUnknownFields(true));

    /// <summary>Serialise a canonical event to its JSON wire form.</summary>
    public static string ToJson(IMessage message) => Formatter.Format(message);

    /// <summary>Parse a JSON wire payload into the given canonical event type.</summary>
    public static T FromJson<T>(string json) where T : IMessage<T>, new() {
        var msg = new T();
        Parser.Merge(json, msg);
        return msg;
    }
}
```

- [ ] **Step 4: Run the test to confirm pass**

```bash
cd schema/dotnet
dotnet test Kurrent.Agent.Schema.slnx --filter "FullyQualifiedName~JsonFormatterTests"
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add schema/dotnet/Kurrent.Agent.Schema/SchemaJsonOptions.cs \
        schema/dotnet/Kurrent.Agent.Schema.Tests/JsonFormatterTests.cs
git commit -m "feat(schema-net): SchemaJsonOptions backed by protobuf JsonFormatter"
```

---

## Task 10: Rewrite `EventTypeMap.cs` and switch `FixtureRoundTripTests` to generated types

**Files:**
- Modify: `schema/dotnet/Kurrent.Agent.Schema/EventTypeMap.cs`
- Delete: `schema/dotnet/Kurrent.Agent.Schema/Events/*.cs`
- Delete: `schema/dotnet/Kurrent.Agent.Schema/TokenUsage.cs`
- Modify: `schema/dotnet/Kurrent.Agent.Schema.Tests/FixtureRoundTripTests.cs`

**Rationale:** Point the registry at generated types, retire the hand-written records, and update the existing fixture test to use `SchemaJsonOptions.ToJson` / `FromJson`.

- [ ] **Step 1: Rewrite `EventTypeMap.cs`**

`schema/dotnet/Kurrent.Agent.Schema/EventTypeMap.cs`:

```csharp
using Google.Protobuf;
using Kurrent.Agent.Schema.Events;

namespace Kurrent.Agent.Schema;

/// <summary>KurrentDB event_type ↔ generated message type registry.</summary>
public static class EventTypeMap {
    public static IReadOnlyDictionary<string, Type> All { get; } = new Dictionary<string, Type> {
        ["SessionStarted"]                = typeof(SessionStarted),
        ["SessionEnded"]                  = typeof(SessionEnded),
        ["SessionContinuedAs"]            = typeof(SessionContinuedAs),
        ["UserMessageReceived"]           = typeof(UserMessageReceived),
        ["AssistantTextGenerated"]        = typeof(AssistantTextGenerated),
        ["AssistantToolCallsGenerated"]   = typeof(AssistantToolCallsGenerated),
        ["AssistantThinkingGenerated"]    = typeof(AssistantThinkingGenerated),
        ["ToolResultReceived"]            = typeof(ToolResultReceived),
        ["InterruptIssued"]               = typeof(InterruptIssued),
        ["InterruptResolved"]             = typeof(InterruptResolved),
        ["SubagentStarted"]               = typeof(SubagentStarted),
        ["SubagentCompleted"]             = typeof(SubagentCompleted),
        ["FactRetained"]                  = typeof(FactRetained),
        ["ArtifactVersionCreated"]        = typeof(ArtifactVersionCreated),
        ["EvalRunStarted"]                = typeof(EvalRunStarted),
        ["TurnScored"]                    = typeof(TurnScored),
        ["EvalRunCompleted"]              = typeof(EvalRunCompleted),
    };
}
```

- [ ] **Step 2: Delete the hand-written event records and TokenUsage**

```bash
rm -r schema/dotnet/Kurrent.Agent.Schema/Events/
rm schema/dotnet/Kurrent.Agent.Schema/TokenUsage.cs
```

- [ ] **Step 3: Rewrite `FixtureRoundTripTests.cs`**

`schema/dotnet/Kurrent.Agent.Schema.Tests/FixtureRoundTripTests.cs`:

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;
using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

public class FixtureRoundTripTests {
    static readonly string FixturesRoot = LocateFixturesRoot();

    public static IEnumerable<object[]> EventFixtureCases() =>
        EventTypeMap.All.Select(pair => new object[] { pair.Key, pair.Value });

    [Theory]
    [MemberData(nameof(EventFixtureCases))]
    public void Event_fixture_round_trips(string eventTypeName, Type clrType) {
        var fixturePath = Path.Combine(FixturesRoot, "events", $"{eventTypeName}.json");
        Assert.True(File.Exists(fixturePath), $"Missing fixture for '{eventTypeName}' at {fixturePath}");

        var originalJson = File.ReadAllText(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        var parseMethod  = typeof(SchemaJsonOptions).GetMethod(nameof(SchemaJsonOptions.FromJson))!
                            .MakeGenericMethod(clrType);
        var parsed = (IMessage)parseMethod.Invoke(null, new object[] { originalJson })!;
        var roundTripJson = SchemaJsonOptions.ToJson(parsed);
        var roundTripNode = JsonNode.Parse(roundTripJson)!;

        AssertStructurallyEqual(originalNode, roundTripNode, eventTypeName);
    }

    [Fact]
    public void Usage_metadata_round_trips() {
        var fixturePath = Path.Combine(FixturesRoot, "metadata", "usage.json");
        var originalJson = File.ReadAllText(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        var parsed = SchemaJsonOptions.FromJson<TokenUsage>(originalJson);
        var roundTripJson = SchemaJsonOptions.ToJson(parsed);
        var roundTripNode = JsonNode.Parse(roundTripJson)!;

        AssertStructurallyEqual(originalNode, roundTripNode, "usage");
    }

    static void AssertStructurallyEqual(JsonNode expected, JsonNode actual, string context) {
        var expectedCanonical = CanonicaliseJson(expected);
        var actualCanonical   = CanonicaliseJson(actual);
        Assert.True(
            expectedCanonical == actualCanonical,
            $"Round-trip drift for {context}:\nExpected:\n{expectedCanonical}\nActual:\n{actualCanonical}"
        );
    }

    static string CanonicaliseJson(JsonNode node) {
        var opts = new JsonSerializerOptions { WriteIndented = false };
        return Canonicalise(node)?.ToJsonString(opts) ?? "null";
    }

    static JsonNode? Canonicalise(JsonNode? node) => node switch {
        null            => null,
        JsonObject obj  => new JsonObject(obj.OrderBy(kv => kv.Key, StringComparer.Ordinal)
                                             .Select(kv => KeyValuePair.Create(kv.Key, Canonicalise(kv.Value)))),
        JsonArray arr   => new JsonArray(arr.Select(Canonicalise).ToArray()),
        JsonValue val   => JsonNode.Parse(val.ToJsonString()),
        _               => node.DeepClone()
    };

    static string LocateFixturesRoot() {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null) {
            var candidate = Path.Combine(dir.FullName, "schema", "fixtures");
            if (Directory.Exists(candidate)) return candidate;

            candidate = Path.Combine(dir.FullName, "fixtures");
            if (Directory.Exists(candidate)
                && File.Exists(Path.Combine(dir.FullName, "SCHEMA_v2.md"))) return candidate;

            dir = dir.Parent;
        }
        throw new DirectoryNotFoundException("Could not locate schema/fixtures relative to test assembly.");
    }
}
```

(The `Additional_counts_preserves_non_snake_case_keys` test from the original file is dropped — `Struct` preserves keys verbatim by construction, so the regression no longer applies. The cross-language test in Task 11 covers this case.)

- [ ] **Step 4: Run tests**

```bash
cd schema/dotnet
dotnet test Kurrent.Agent.Schema.slnx -c Release
```

Expected: all tests PASS. If a fixture fails, inspect the diff — it should be a casing/null-omission delta, not a semantic difference. Re-run Python's `regen_fixtures.py` from a previous task only if the Python tests still pass on the regenerated form — both languages must agree on the canonical form.

- [ ] **Step 5: Commit**

```bash
git rm -r schema/dotnet/Kurrent.Agent.Schema/Events/
git rm schema/dotnet/Kurrent.Agent.Schema/TokenUsage.cs
git add schema/dotnet/Kurrent.Agent.Schema/EventTypeMap.cs \
        schema/dotnet/Kurrent.Agent.Schema.Tests/FixtureRoundTripTests.cs
git commit -m "refactor(schema-net): swap hand-written records for generated protobuf types"
```

---

## Task 11: Add cross-language byte-equal output test

**Files:**
- Create: `schema/python/tests/dump_canonical_output.py`
- Create: `schema/dotnet/Kurrent.Agent.Schema.Tests/DumpCanonicalOutput.cs` (new xunit class) — or a small driver console program if preferred
- Create: `.github/workflows/schema-cross-language.yml`

**Rationale:** Both languages re-serialise the same input (the existing `schema/fixtures/events/*.json` corpus) using their sanctioned helper, write the output to disk, and a CI job diffs the two output trees. If both writers produce byte-equal JSON for every fixture, cross-language parity is proven (each side already round-trips against itself in Tasks 7 and 10; equal writer outputs across languages is the missing third invariant). Catches "forgot a flag" bugs that the per-language tests miss.

- [ ] **Step 1: Write the Python output dumper**

`schema/python/tests/dump_canonical_output.py`:

```python
"""Round-trip every fixture through to_json() and write the result to a
disk path. The companion .NET program does the same. CI diffs the two
output directories — they MUST be byte-equal.

Usage: uv run python tests/dump_canonical_output.py <output_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from kurrent_agent_schema import TokenUsage, from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: dump_canonical_output.py <output_dir>")
    out_root = Path(sys.argv[1])
    (out_root / "events").mkdir(parents=True, exist_ok=True)
    (out_root / "metadata").mkdir(parents=True, exist_ok=True)

    for path in sorted((FIXTURES / "events").glob("*.json")):
        cls = EVENT_TYPE_BY_NAME[path.stem]
        parsed = from_json(cls, path.read_text())
        (out_root / "events" / path.name).write_text(to_json(parsed))

    usage_path = FIXTURES / "metadata" / "usage.json"
    parsed = from_json(TokenUsage, usage_path.read_text())
    (out_root / "metadata" / "usage.json").write_text(to_json(parsed))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the .NET output dumper as an xunit "test" that always emits files**

`schema/dotnet/Kurrent.Agent.Schema.Tests/DumpCanonicalOutput.cs`:

```csharp
using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

/// <summary>
/// Round-trip every fixture through SchemaJsonOptions.ToJson and write the
/// result under the directory named by the SCHEMA_DUMP_OUT environment
/// variable. CI diffs this against the Python dumper output. Skipped
/// (no-op) when the env var is not set, so normal test runs are unaffected.
/// </summary>
public class DumpCanonicalOutput {
    [Fact]
    public void Dump_canonical_output_when_env_set() {
        var outRoot = Environment.GetEnvironmentVariable("SCHEMA_DUMP_OUT");
        if (string.IsNullOrEmpty(outRoot)) return;

        var fixturesRoot = LocateFixtures();
        Directory.CreateDirectory(Path.Combine(outRoot, "events"));
        Directory.CreateDirectory(Path.Combine(outRoot, "metadata"));

        var fromJson = typeof(SchemaJsonOptions).GetMethod(nameof(SchemaJsonOptions.FromJson))!;

        foreach (var (eventName, clrType) in EventTypeMap.All) {
            var src = File.ReadAllText(Path.Combine(fixturesRoot, "events", $"{eventName}.json"));
            var parsed = (IMessage)fromJson.MakeGenericMethod(clrType).Invoke(null, new object[] { src })!;
            File.WriteAllText(Path.Combine(outRoot, "events", $"{eventName}.json"),
                              SchemaJsonOptions.ToJson(parsed));
        }

        var usageSrc = File.ReadAllText(Path.Combine(fixturesRoot, "metadata", "usage.json"));
        var usage = SchemaJsonOptions.FromJson<TokenUsage>(usageSrc);
        File.WriteAllText(Path.Combine(outRoot, "metadata", "usage.json"),
                          SchemaJsonOptions.ToJson(usage));
    }

    static string LocateFixtures() {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null) {
            var candidate = Path.Combine(dir.FullName, "schema", "fixtures");
            if (Directory.Exists(candidate)) return candidate;
            candidate = Path.Combine(dir.FullName, "fixtures");
            if (Directory.Exists(candidate)
                && File.Exists(Path.Combine(dir.FullName, "SCHEMA_v2.md"))) return candidate;
            dir = dir.Parent;
        }
        throw new DirectoryNotFoundException("schema/fixtures not found");
    }
}
```

- [ ] **Step 3: Locally verify both dumpers produce byte-equal output**

```bash
mkdir -p /tmp/schema-cross-py /tmp/schema-cross-net

cd schema/python
uv run python tests/dump_canonical_output.py /tmp/schema-cross-py

cd ../dotnet
SCHEMA_DUMP_OUT=/tmp/schema-cross-net dotnet test Kurrent.Agent.Schema.slnx \
  --filter "FullyQualifiedName~DumpCanonicalOutput"

diff -r /tmp/schema-cross-py /tmp/schema-cross-net
```

Expected: `diff` exits 0, no differences. If differences, examine — the most likely cause is a flag mismatch between the two helpers. Fix `json.py` or `SchemaJsonOptions.cs` until the diff is clean.

- [ ] **Step 4: Write the cross-language CI workflow**

`.github/workflows/schema-cross-language.yml`:

```yaml
name: Schema cross-language parity

on:
  pull_request:
    paths:
      - 'schema/**'
      - '.github/workflows/schema-cross-language.yml'
  push:
    branches: [main]
    paths:
      - 'schema/**'
      - '.github/workflows/schema-cross-language.yml'

concurrency:
  group: schema-cross-language-${{ github.ref }}
  cancel-in-progress: true

jobs:
  parity:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - uses: astral-sh/setup-uv@v8.1.0
        with:
          python-version: '3.11'
          enable-cache: true

      - uses: actions/setup-dotnet@v5
        with:
          dotnet-version: '10.0.x'

      - name: Sync Python deps
        working-directory: schema/python
        run: uv sync --all-extras --frozen

      - name: Dump Python output
        working-directory: schema/python
        run: uv run python tests/dump_canonical_output.py ${{ runner.temp }}/py-out

      - name: Build .NET
        working-directory: schema/dotnet
        run: dotnet build Kurrent.Agent.Schema.slnx -c Release

      - name: Dump .NET output
        working-directory: schema/dotnet
        env:
          SCHEMA_DUMP_OUT: ${{ runner.temp }}/net-out
        run: dotnet test Kurrent.Agent.Schema.slnx -c Release --no-build --filter "FullyQualifiedName~DumpCanonicalOutput"

      - name: Compare outputs
        run: diff -r ${{ runner.temp }}/py-out ${{ runner.temp }}/net-out
```

- [ ] **Step 5: Commit**

```bash
git add schema/python/tests/dump_canonical_output.py \
        schema/dotnet/Kurrent.Agent.Schema.Tests/DumpCanonicalOutput.cs \
        .github/workflows/schema-cross-language.yml
git commit -m "test(schema): cross-language byte-equal output diff"
```

---

## Task 12: CI — codegen drift gate, buf lint/breaking, cross-language job

**Files:**
- Create: `.github/workflows/schema-codegen.yml` (replaces nothing; new workflow)
- Modify: `.github/workflows/schema-ci-python.yml` (path triggers)
- Modify: `.github/workflows/schema-ci-dotnet.yml` (path triggers)

**Rationale:** Lock the workflow: any proto change must regenerate code; any generated-code change without a proto change fails CI; any breaking proto change without a major version bump fails CI.

- [ ] **Step 1: Add path triggers for `schema/proto/**` to existing workflows**

In `.github/workflows/schema-ci-python.yml`, expand the `paths:` block under both `pull_request` and `push`:

```yaml
paths:
  - 'schema/proto/**'
  - 'schema/python/**'
  - 'schema/fixtures/**'
  - '.github/workflows/schema-ci-python.yml'
```

Same change in `.github/workflows/schema-ci-dotnet.yml`.

- [ ] **Step 2: Create the codegen drift workflow**

`.github/workflows/schema-codegen.yml`:

```yaml
name: Schema codegen

on:
  pull_request:
    paths:
      - 'schema/proto/**'
      - 'schema/buf.yaml'
      - 'schema/buf.gen.yaml'
      - 'schema/python/kurrent_agent_schema/_generated/**'
      - 'schema/dotnet/Kurrent.Agent.Schema/Generated/**'
      - '.github/workflows/schema-codegen.yml'
  push:
    branches: [main]
    paths:
      - 'schema/proto/**'
      - 'schema/buf.yaml'
      - 'schema/buf.gen.yaml'
      - '.github/workflows/schema-codegen.yml'

concurrency:
  group: schema-codegen-${{ github.ref }}
  cancel-in-progress: true

jobs:
  drift:
    name: Drift gate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - uses: bufbuild/buf-setup-action@v1
        with:
          version: '1.50.0'

      - name: Lint
        working-directory: schema
        run: buf lint

      - name: Generate
        working-directory: schema
        run: buf generate

      - name: Fail on diff
        run: git diff --exit-code

  breaking:
    name: Breaking-change check
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - uses: bufbuild/buf-setup-action@v1
        with:
          version: '1.50.0'

      - name: Breaking
        working-directory: schema
        run: buf breaking --against ".git#branch=main,subdir=schema"
```

- [ ] **Step 3: Verify the workflow file parses**

```bash
yq . .github/workflows/schema-codegen.yml > /dev/null
```

Expected: no error. (If `yq` is not installed, use any YAML linter.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/schema-ci-python.yml \
        .github/workflows/schema-ci-dotnet.yml \
        .github/workflows/schema-codegen.yml
git commit -m "ci(schema): add codegen drift gate + breaking-change check"
```

---

## Task 13: Bump versions and write release notes

**Files:**
- Modify: `schema/python/pyproject.toml`
- Modify: `schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj`
- Modify: `schema/python/kurrent_agent_schema/version.py` (only if it carries the package version)
- Create: `schema/python/CHANGELOG.md` (if missing)
- Create: `schema/dotnet/Kurrent.Agent.Schema/CHANGELOG.md` (if missing)
- Modify: `schema/python/README.md` (note the new dependency)
- Modify: `schema/dotnet/Kurrent.Agent.Schema/README.md` (note the new dependency)

**Rationale:** Final release prep. Bumps both packages from `0.1.2` → `0.2.0`. Documents the wire-shape change and the new runtime dependency.

- [ ] **Step 1: Bump Python version**

In `schema/python/pyproject.toml`, change:

```toml
version = "0.2.0"
```

- [ ] **Step 2: Bump .NET version**

In `schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj`, change:

```xml
<Version>0.2.0</Version>
```

- [ ] **Step 3: Confirm `SCHEMA_VERSION` constant**

`schema/python/kurrent_agent_schema/version.py` should still read `SCHEMA_VERSION = 2` — that's the **schema** version (proto package `kurrent.agent.v2`), not the package version. No change needed unless the file conflates the two. Read and confirm.

- [ ] **Step 4: Write release notes for Python**

Create or append to `schema/python/CHANGELOG.md`:

```markdown
# Changelog

## 0.2.0

**Breaking** (pre-1.0):

- Schema source moved from hand-maintained Pydantic models to generated
  Protobuf message classes. Public class names unchanged; consumers using
  `from kurrent_agent_schema import SessionStarted` continue to import the
  same name, now backed by `google.protobuf.Message`.
- New runtime dependency: `protobuf >= 5.27`. `pydantic` dependency dropped.
- JSON wire shape: unset optional fields are now omitted instead of emitted
  as explicit `null`. Existing readers tolerate both.
- Sanctioned JSON entry point: `to_json(event)` and `from_json(cls, src)`.
  Direct calls to `google.protobuf.json_format` are not supported.
```

- [ ] **Step 5: Write release notes for .NET**

Create or append to `schema/dotnet/Kurrent.Agent.Schema/CHANGELOG.md`:

```markdown
# Changelog

## 0.2.0

**Breaking** (pre-1.0):

- Schema source moved from hand-written records to generated Protobuf
  message classes. Namespaces preserved: events and value types remain
  under `Kurrent.Agent.Schema.Events`, `TokenUsage` remains under
  `Kurrent.Agent.Schema`. Consumers' `using` lines don't change.
- New runtime dependency: `Google.Protobuf` 3.28+. `System.Text.Json`-based
  serialisation removed.
- JSON wire shape: unset optional fields are now omitted instead of emitted
  as explicit `null`. Existing readers tolerate both.
- Sanctioned JSON entry point: `SchemaJsonOptions.ToJson(message)` and
  `SchemaJsonOptions.FromJson<T>(src)`. Direct calls to
  `Google.Protobuf.JsonFormatter` are not supported.
```

- [ ] **Step 6: Update READMEs**

Add a short note to each README under a "Wire format" or similar heading: "Backed by Protobuf codegen from `schema/proto/`. JSON output uses proto3 canonical mapping with `preserve_proto_field_name`."

- [ ] **Step 7: Final test pass on both languages**

```bash
cd schema/python && uv run pytest -v
cd schema/dotnet && dotnet test Kurrent.Agent.Schema.slnx -c Release
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add schema/python/pyproject.toml \
        schema/dotnet/Kurrent.Agent.Schema/Kurrent.Agent.Schema.csproj \
        schema/python/CHANGELOG.md \
        schema/dotnet/Kurrent.Agent.Schema/CHANGELOG.md \
        schema/python/README.md \
        schema/dotnet/Kurrent.Agent.Schema/README.md
git commit -m "release(schema): bump to 0.2.0 with protobuf source"
```

---

## Task 14: Update `SCHEMA_v2.md §10 Q1` to reflect the new resolution

**Files:**
- Modify: `schema/SCHEMA_v2.md`

**Rationale:** Open Question #1 was resolved on 2026-04-21 in favour of hand-maintained parallel packages. This migration flips that resolution.

- [ ] **Step 1: Edit `SCHEMA_v2.md §10 Q1`**

Replace the existing resolution text with:

```markdown
1. ~~**Single shared package for canonical types.**~~ ✅ **Resolved 2026-04-21**, **revised 2026-04-27** (DEV-XXXX). Source of truth is now Protobuf at `schema/proto/kurrent/agent/v2/`. The Python (`kurrent-agent-schema`) and .NET (`Kurrent.Agent.Schema`) packages are generated by `buf` (committed `_generated/` and `Generated/` trees) plus a thin hand-written sibling for stream-name builders, JSON helpers, and the type registry. JSON wire format is proto3 canonical with `preserve_proto_field_name=true`. Drift guarded by per-language fixture round-trip tests, a cross-language round-trip job, and a buf-generate diff gate in CI. See `docs/superpowers/specs/2026-04-27-protobuf-schema-design.md`.
```

(Replace `DEV-XXXX` with the actual Linear issue once it's filed.)

- [ ] **Step 2: Commit**

```bash
git add schema/SCHEMA_v2.md
git commit -m "docs(schema): record protobuf-source resolution for Q1"
```

---

## Self-review checklist

After implementing the plan, run through:

1. **Spec coverage:** every section of `docs/superpowers/specs/2026-04-27-protobuf-schema-design.md` is implemented somewhere above.
   - §3 approach: Tasks 1–4 land protos, Tasks 5–10 swap package internals.
   - §4 layout: Tasks 1–4.
   - §5 package structure: Tasks 5, 6, 8, 9, 10.
   - §6 codegen workflow: Tasks 1, 12.
   - §7 testing strategy: Tasks 7, 10, 11; CI in Task 12.
   - §8 wire-format compatibility: Task 7 step 6 (fixture regen) and Task 13 (release notes).
   - §9 migration plan: full plan covers steps 1–4. Steps 5 (integrations bump dep) and 6 (Capacitor) are out of scope here, since the API surface is unchanged and integrations rebuild without code changes.
2. **Public API parity:** `from kurrent_agent_schema import SessionStarted` and `using Kurrent.Agent.Schema; new SessionStarted(...)` continue to work.
3. **No-protoc consumer:** `pip install kurrent-agent-schema==0.2.0` and `dotnet add package Kurrent.Agent.Schema --version 0.2.0` install clean without protoc.
4. **Cross-language byte-equality** verified by Task 11 in both directions.
