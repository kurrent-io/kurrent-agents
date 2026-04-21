# Schema v2 JSON fixtures

Canonical reference JSON for every event type, metadata block, and documented extension-block shape in the v2 schema.

These fixtures are the **drift-detection source of truth** between `schema/python/kurrent_agent_schema/` and `schema/dotnet/Kurrent.Agent.Schema/`. Both packages run a round-trip test:

1. Load the fixture JSON.
2. Deserialise into the canonical model for that event type.
3. Serialise the model back to JSON.
4. Assert equality with the original (normalised for key order / whitespace).

A PR that changes a canonical field name, type, or default must update the matching fixture — otherwise the drift tests in both packages fail and CI rejects the change. A PR that changes the Python model but not the .NET model (or vice versa) also fails.

## Layout

```
schema/fixtures/
  events/                 # one <EventType>.json per canonical event
    SessionStarted.json
    AssistantTextGenerated.json
    ...
  metadata/
    usage.json            # $usage KurrentDB metadata shape
  extensions/             # example extension blocks, one per documented slug
    adk.json
    afw.json
    claude_code.json
    openai.json
    strands.json
```

## JSON format

- All keys snake_case.
- Timestamps are ISO-8601 with timezone (`"2026-04-21T10:00:00+00:00"`).
- Every canonical event includes an `extensions` block populated with at least one slug, even when the canonical event type is slug-agnostic — this exercises the envelope on every event.
- Fields with `null` are omitted from fixtures (readers tolerate absence; writers should not emit nulls for unset optional fields).

See `schema/SCHEMA_v2.md` for field-level semantics.
