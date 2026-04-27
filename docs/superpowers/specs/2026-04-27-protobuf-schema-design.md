# Protobuf as canonical schema source — design

**Status:** draft, ready for implementation planning.
**Supersedes:** schema doc Open Question #1 ([`schema/SCHEMA_v2.md §10`](../../../schema/SCHEMA_v2.md)), previously resolved 2026-04-21 in favour of hand-maintained parallel packages.
**Linear:** TBD (new issue to be opened; DEV-1524 reopened in concept).

## 1. Problem

Today the canonical event schema (`schema/SCHEMA_v2.md`) is hand-maintained twice:

- `schema/python/kurrent_agent_schema/` — Pydantic models, published to PyPI
- `schema/dotnet/Kurrent.Agent.Schema/` — C# records, published to NuGet

Drift between the two is guarded by JSON-fixture round-trip tests in CI. Adding a new language stack (Go, TypeScript, Rust, …) means hand-translating the schema a third, fourth, fifth time. Each new language is another drift surface and another release coordination point.

This design replaces the two parallel hand-maintained packages with a single Protobuf source of truth that generates language packages.

## 2. Non-goals

- **Not changing the wire format.** KurrentDB events stay JSON. Protobuf binary on the wire is out of scope (would be a breaking change for every integration and every reader).
- **Not redesigning the canonical schema.** Field names, semantics, and stream conventions in `SCHEMA_v2.md §3` are unchanged. This is purely a source-of-truth migration.
- **Not adding new language packages now.** v1 publishes Python + .NET only, replacing the existing packages. Go, TS, etc. follow when a real consumer appears.
- **Not migrating existing on-disk events.** Readers tolerate the small wire-shape shift (null → omitted on optional fields) under the existing forward-compatibility rules.

## 3. Approach

**Schema-as-spec, JSON on the wire (unchanged).** Protobuf becomes the source of truth for *types*; codegen produces language packages whose JSON output uses the proto3 JSON canonical mapping with `preserve_proto_field_name` enabled. KurrentDB events stay JSON.

Two alternatives were considered and rejected:

- **Protobuf binary on the wire** — breaking for every integration and every reader. Capacitor could migrate cheaply but the cost is concentrated in the framework integrations and out-of-tree consumers, where the migration window is unbounded.
- **Hybrid: hand-written wrappers per language atop generated types** — defeats the point of moving to a single source. Adds the same drift surface back in a different shape.

## 4. Source-of-truth layout

```
schema/
├── proto/
│   └── kurrent/agent/v2/
│       ├── events.proto         # all canonical events
│       ├── value_types.proto    # AgentConfig, ToolSpec, ToolCallInfo
│       └── usage.proto          # TokenUsage (rides on $usage metadata)
├── buf.yaml
├── buf.gen.yaml
├── fixtures/                    # unchanged, drives cross-lang tests
├── dotnet/Kurrent.Agent.Schema/
└── python/kurrent_agent_schema/
```

### 4.1 Proto modelling choices

| Schema concept | Proto representation | JSON shape |
|---|---|---|
| `extensions: dict[str, dict[str, Any]] \| None` | `map<string, google.protobuf.Struct> extensions = N;` | Plain object, `extensions.<slug>.<...>` round-trips losslessly |
| Nullable scalars (`content`, `prompt`, `agent_name`, …) | `optional string content = N;` (proto3 optional keyword) | Field omitted when unset; present when set (including empty string) |
| `datetime` | `google.protobuf.Timestamp timestamp = N;` | RFC 3339 string with `Z` suffix |
| `bytes` (e.g. `inline_bytes`) | `bytes inline_bytes = N;` | base64 string |
| Open-string fields (`kind`, `outcome`, `agent_type`, `reason`) | `string kind = N;` | String — closed enums rejected because schema requires reader tolerance for unknown values |
| Free-form JSON dicts (`tool_calls[].arguments`, `tools[].input_schema`, `custom_metadata`) | `google.protobuf.Struct` | Plain object |
| `additional_counts` (in `TokenUsage`) | `map<string, int64>` | Plain object with integer values |
| Repeated value types | `repeated ToolCallInfo tool_calls = N;` | JSON array |

### 4.2 Field-name casing

Proto3 JSON defaults to `camelCase`. Current wire is `snake_case`. Every emitter is configured with `preserve_proto_field_name` (or its language-specific equivalent) at the *helper* layer, not at the call site — see §5.

## 5. Per-language package structure

Each published package combines **generated message types** with a **thin hand-written sibling** for things proto cannot express (stream-name builders, type registries, JSON serializer config, version constants).

```
python/kurrent_agent_schema/
├── _generated/                  # buf-generated, committed to repo
│   └── kurrent/agent/v2/...
├── __init__.py                  # re-exports the public surface
├── streams.py                   # agent_session_stream(), agent_subsession_stream(), …
├── usage.py                     # USAGE_METADATA_KEY, TokenUsage helpers
├── version.py                   # SCHEMA_VERSION = 2
├── json.py                      # to_json(event) / from_json(cls, s)
└── registry.py                  # EVENT_TYPE_NAMES, EVENT_TYPE_BY_NAME
```

The .NET package mirrors this layout under `dotnet/Kurrent.Agent.Schema/` (`Generated/`, `StreamNames.cs`, `TokenUsage.cs`, `SchemaVersion.cs`, `SchemaJsonOptions.cs`, `EventTypeMap.cs`).

### 5.1 The single sanctioned JSON entry point

`json.py` and `SchemaJsonOptions.cs` are the only places that call into the protobuf JSON formatter. Both bake in:

- `preserve_proto_field_name = true`
- Timestamp format = RFC 3339
- Bytes format = base64

Integrations call `to_json(event)` / `SchemaJsonOptions.Serialize(event)` and never touch the raw protobuf JSON formatter. CI lints for direct calls to `MessageToJson` / `JsonFormatter` outside the helper module. This makes "forgot the casing flag" structurally impossible — there is no call site at which it could be forgotten.

### 5.2 Public surface compatibility

The package's public surface (class names, module paths, constructor parameter names) stays identical to today's hand-maintained version. Integrations consuming `from kurrent_agent_schema import SessionStarted` continue to import the same name, now backed by a generated class re-exported through `__init__.py`. The migration is internal.

## 6. Codegen workflow

**Generated code is committed to the repo.** Justifications:

- Downstream consumers (other integrations in this repo, Capacitor, future external consumers) install via normal `pip install` / NuGet. No protoc on consumer machines.
- Wire-format changes appear in PR diffs. A generated-code change without a corresponding proto change is a code-review red flag.
- Published wheels and nupkgs ship the generated source — no `setup.py` codegen step at install time.

### 6.1 CI drift gate

```yaml
- run: buf generate
- run: git diff --exit-code
```

If a contributor edits a `.proto` and forgets to regenerate, CI fails. If someone edits generated code by hand, CI fails the same way. Generated tree cannot drift from its source.

### 6.2 Local dev flow

A single command (e.g. a `make schema` target or equivalent script under `schema/`) runs `buf generate` and rewrites the committed generated trees. Exact entry point chosen at implementation time. `buf` is added to `.config/dotnet-tools.json` or installed via Homebrew. No global `protoc` installation is required.

### 6.3 Versioning & publishing

- `schema/python/pyproject.toml` and `Kurrent.Agent.Schema.csproj` keep independent version numbers (matches the current release flow).
- PyPI publish on `schema-py-vX.Y.Z` tag, NuGet on `schema-net-vX.Y.Z` tag.
- Major version bump (2.0.0 on each side) lands the cutover.
- `buf breaking --against '.git#branch=main'` fails any PR that breaks wire compatibility on tagged-released paths.

## 7. Testing strategy

Three layers, all running on every PR:

### 7.1 Per-language round-trip

Each language tests itself against `schema/fixtures/`:

- Read fixture JSON → message → JSON → must equal input.
- Existing `Kurrent.Agent.Schema.Tests` and `schema/python/tests/` extend to use the new generated types.

### 7.2 Cross-language round-trip

Catches "forgot a flag" bugs that per-language tests would miss:

- Python writes the canonical-event corpus as JSON → .NET reads it, re-serializes → byte-equal to Python's output.
- The inverse: .NET writes → Python reads → byte-equal.
- One CI job per direction.

### 7.3 Buf breaking-change lint

`buf breaking --against '.git#branch=main'` runs on every PR. Fails any PR that breaks wire compatibility unless the PR explicitly bumps a major version.

### 7.4 Fixture regeneration

The cutover regenerates `schema/fixtures/*.json` once because optional fields shift from `"x": null` to no `x` key (proto3 JSON canonical form omits unset optionals). That commit is the explicit migration boundary — readers built against pre-cutover fixtures keep working under `extra="ignore"` / `JsonUnmappedMemberHandling.Skip`, but new fixtures reflect the proto3 JSON canonical shape.

## 8. Wire-format compatibility notes

| Aspect | Pre-cutover | Post-cutover | Reader impact |
|---|---|---|---|
| Field name casing | snake_case | snake_case (via `preserve_proto_field_name`) | None |
| Unset optional scalar | `"x": null` (Pydantic default) | omitted | None — readers tolerate both |
| Unset `extensions` | `"extensions": null` or absent | omitted (empty map encodes as absent in proto3 JSON) | None |
| Datetime | ISO 8601 (Pydantic default) | RFC 3339 with `Z` (`google.protobuf.Timestamp`) | None — both parse |
| Bytes | base64 | base64 | None |
| Open-string fields | string | string | None |

No reader change is required. The shift is exclusively in writer output, and is absorbed by the existing forward-compatibility semantics documented in `SCHEMA_v2.md §9`.

## 9. Migration plan

1. **Land protos & generators in this repo.** `schema/proto/`, `buf.yaml`, `buf.gen.yaml`, generated trees committed. Both packages still publish hand-written code; new packages not yet released.
2. **Switch package internals to generated types.** `kurrent_agent_schema` and `Kurrent.Agent.Schema` are re-implemented atop the generated messages, keeping the same public import surface (class names, module paths, constructor signatures).
3. **Cross-language fixture tests pass.** Including the cross-write/cross-read job. Fixtures regenerated once.
4. **Version bump.** Both packages are currently at `0.1.2`. The cutover ships as a minor bump (`0.2.0`) since SemVer pre-1.0 allows breaking changes in minors and the package has no out-of-tree consumers yet. Changelog notes the null-to-omitted wire-shape shift and the new runtime dependency on `google.protobuf` / `Google.Protobuf`.
5. **Each integration in this repo bumps its dependency.** Mostly a no-op since type names did not change. CI run per integration to confirm.
6. **Capacitor follows separately** on its own timeline. Pre-existing `kapacitor history` reimport remains the migration tool for any drifted streams.

`SCHEMA_v2.md §10 Q1` is updated to point at this design as the new resolution.

## 10. Open questions

None blocking. All foundational choices (wire format unchanged, JSON transcoding via proto3 mapping, package replacement in-place, language scope = Python + .NET) are decided in §3.

## 11. Out of scope (revisit later)

- **Go and TypeScript packages.** Adding a buf plugin + publish job per language is a few hours of work; defer until a real consumer surfaces.
- **Protobuf binary on the wire.** Would be a breaking change for every integration and every reader. Not justified by current performance or size needs.
- **Schema versioning beyond v2.** This design preserves v2; a future v3 would follow the same source-of-truth-in-proto pattern from day one.
