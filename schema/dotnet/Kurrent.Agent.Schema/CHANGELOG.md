# Changelog

## 0.5.0

- Add `ToolCallInfo.ToolKind` (field 4, `string`) — a vendor-neutral
  classification of what a tool call does, using ACP's `ToolKind`
  vocabulary (`read`, `edit`, `delete`, `move`, `search`, `execute`,
  `think`, `fetch`, `switch_mode`, `other`). Consumers that need to know
  what a call *did* read this instead of keeping a per-vendor tool-name
  table; `ToolName` stays raw vendor fidelity.
- The field has explicit presence (`HasToolKind` / `ClearToolKind`), so
  **absent stays distinguishable from `"other"`**: absent means nobody
  classified the call, `"other"` means classified and none of the above.
  Producers with no classification leave the field unset (`ToolKind` is
  then omitted by `SchemaJsonOptions.ToJson`) — they must not write `""`
  or substitute `"other"`. See `SCHEMA_v2.md §3.4.1`.
- Additive and optional: existing writers and readers are unaffected,
  and 0.4.1 payloads parse unchanged.

## 0.3.0

- Migrate v2 protos from `proto3` to `edition = "2024"`. Edition 2024
  makes EXPLICIT field presence the default, so every scalar field —
  including ones that previously had implicit presence (`MessageIndex`,
  `Version`, `Score`, `Encrypted`, …) — now exposes `HasX` / `ClearX`
  accessors. Wire format is unchanged; field numbers, types, and names
  are identical, and existing consumer code keeps compiling against the
  same property names.
- Bump `Google.Protobuf` runtime floor to `3.34.1` (was `3.28.3`). This
  is the runtime that pairs with protoc 34 / Edition 2024 generated
  code. Consumers must update accordingly.

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
- `int64` fields (e.g. `TokenUsage.InputTokens`) are now serialised as JSON
  strings per the proto3 JSON canonical mapping (`"1507"` instead of `1507`).
  Generated parsers on either side handle this transparently.
- Sanctioned JSON entry point: `SchemaJsonOptions.ToJson(message)` and
  `SchemaJsonOptions.FromJson<T>(src)`. Direct calls to
  `Google.Protobuf.JsonFormatter` are not supported.
