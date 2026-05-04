# kurrent-agents

KurrentDB integrations for AI agent frameworks, sharing a canonical event schema so that a session written by one framework is readable by another.

**Status: early. Spike-to-alpha.** Breaking changes expected.

The canonical event schema (schema v2) is published as `kurrent-agent-schema` (PyPI) and `Kurrent.Agent.Schema` (NuGet); both are generated from the same Protobuf definitions in [`schema/proto/`](./schema/proto/). The .NET integration `Kurrent.AgentFramework` is also published on NuGet. Other integrations are installed from source.

## Layout

```
schema/                              canonical event schema (the source of truth)
  SCHEMA.md                          v1 spec (historical / reference)
  SCHEMA_v2.md                       v2 spec (current)
  proto/                             Protobuf source — shared codegen input
  fixtures/                          JSON fixtures for cross-language drift tests
  python/                            kurrent-agent-schema (Protobuf-gen Python)
  dotnet/                            Kurrent.Agent.Schema (Protobuf-gen .NET)

google-adk/
  python/                            Kurrent integration for Google ADK (Python)

microsoft-agent-framework/
  python/                            Kurrent integration for MS Agent Framework (Python)
  dotnet/                            Kurrent integration for MS Agent Framework (.NET)

strands/
  python/                            Kurrent integration for Strands Agents SDK (Python)

openai-agents/
  python/                            Kurrent integration for OpenAI Agents SDK (Python)

claude-agent-sdk/
  python/                            Kurrent SessionStore adapter for Claude Agent SDK (Python)

testing/                             shared pytest fixtures + Testcontainers wrapper
                                     (path dep, not published)
```

## Packages

| Path | Package | Language | Status |
|---|---|---|---|
| `schema/python` | `kurrent-agent-schema` | Python | published on PyPI (0.4.0) |
| `schema/dotnet` | `Kurrent.Agent.Schema` | C# | published on NuGet (0.4.0) |
| `microsoft-agent-framework/dotnet` | `Kurrent.AgentFramework` | C# | published on NuGet (0.4.1) |
| `microsoft-agent-framework/python` | `kurrent-agent-framework` | Python | alpha (source) |
| `google-adk/python` | `kurrent-google-adk` | Python | alpha (source) |
| `strands/python` | `kurrent-strands` | Python | alpha (source) |
| `openai-agents/python` | `kurrent-openai-agents` | Python | alpha (source) |
| `claude-agent-sdk/python` | `kurrent-claude-agent-sdk` | Python | alpha (source) |
| `testing` | `kurrent-agents-testing` | Python | internal path dep |

## Getting started

Each framework integration is a standalone package. See the README inside each subfolder for build and usage instructions. Each package ships with its own `pyproject.toml` / `.slnx` as appropriate.

A running KurrentDB instance is required. Most subfolders provide a `docker-compose.yml` you can use locally.

## Why a monorepo

The canonical event schema (`schema/SCHEMA_v2.md`) is shared across every integration. Keeping everything in one repo makes drift mechanically impossible: a schema change fails every package's CI at once, and shared JSON fixtures under `schema/fixtures/` round-trip in every language to catch wire drift.

Per-language tooling (`uv` for Python, NuGet for .NET) coexists via path-filtered CI workflows. Each package has independent versioning and release cadence.

## License

Apache License 2.0 — see [LICENSE](./LICENSE).
