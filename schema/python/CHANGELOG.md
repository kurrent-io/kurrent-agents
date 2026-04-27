# Changelog

## 0.2.0

**Breaking** (pre-1.0):

- Schema source moved from hand-maintained Pydantic models to generated
  Protobuf message classes. Public class names unchanged; consumers using
  `from kurrent_agent_schema import SessionStarted` continue to import the
  same name, now backed by `google.protobuf.Message`.
- New runtime dependency: `protobuf >= 5.27, < 8`. `pydantic` dependency
  dropped.
- JSON wire shape: unset optional fields are now omitted instead of emitted
  as explicit `null`. Existing readers tolerate both.
- `int64` fields (e.g. `TokenUsage.input_tokens`) are now serialised as JSON
  strings per the proto3 JSON canonical mapping (`"1507"` instead of `1507`).
  Generated parsers on either side handle this transparently; raw-JSON
  consumers may need to cast.
- Numeric values inside `extensions` and `additional_counts` (modelled as
  `google.protobuf.Struct`) round-trip through `Value.number_value`, which
  is a `double`. Whole-number values may serialise with a `.0` suffix.
- Sanctioned JSON entry point: `to_json(event)` and `from_json(cls, src)`.
  Direct calls to `google.protobuf.json_format` are not supported.
