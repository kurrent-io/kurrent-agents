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
- `int64` fields (e.g. `TokenUsage.InputTokens`) are now serialised as JSON
  strings per the proto3 JSON canonical mapping (`"1507"` instead of `1507`).
  Generated parsers on either side handle this transparently.
- Sanctioned JSON entry point: `SchemaJsonOptions.ToJson(message)` and
  `SchemaJsonOptions.FromJson<T>(src)`. Direct calls to
  `Google.Protobuf.JsonFormatter` are not supported.
