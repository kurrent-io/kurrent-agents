# kurrent-agents

KurrentDB integrations for AI agent frameworks, sharing a canonical event schema so that a session written by one framework is readable by another.

**Status: early. Spike-to-alpha.** Breaking changes expected.

## Layout

```
schema/                              canonical event schema (the source of truth)
  SCHEMA.md                          human-readable spec
  python/     (planned)              kurrent-agent-schema (Pydantic v2)
  dotnet/     (planned)              Kurrent.AgentSchema (System.Text.Json)
  ...

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

# planned: langchain/, ...

interop-tests/                       cross-framework round-trip tests
docs/                                unified documentation
```

## Packages

| Path | Package | Language | Status |
|---|---|---|---|
| `microsoft-agent-framework/dotnet` | `Kurrent.AgentFramework` | C# | alpha |
| `microsoft-agent-framework/python` | `kurrent-agent-framework` | Python | spike |
| `google-adk/python` | `kurrent-google-adk` | Python | alpha |
| `strands/python` | `kurrent-strands` | Python | scaffolding |
| `openai-agents/python` | `kurrent-openai-agents` | Python | scaffolding |
| `claude-agent-sdk/python` | `kurrent-claude-agent-sdk` | Python | scaffolding |

## Getting started

Each framework integration is a standalone package. See the README inside each subfolder for build and usage instructions. All packages ship with their own `pyproject.toml` / `.slnx` / `package.json` as appropriate.

A running KurrentDB instance is required. Most subfolders provide a `docker-compose.yml` you can use locally.

## Why a monorepo

The canonical event schema (`schema/SCHEMA.md`) is shared across every integration. Keeping everything in one repo makes drift mechanically impossible: a schema change fails every package's CI at once, and cross-framework interop tests naturally exercise multiple integrations in a single run.

Per-language tooling (`uv` for Python, NuGet for .NET, etc.) coexists via path-filtered CI workflows. Each package has independent versioning and release cadence.

## License

Apache License 2.0 — see [LICENSE](./LICENSE).
