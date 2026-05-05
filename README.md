# kurrent-agents

KurrentDB integrations for AI agent frameworks, sharing a canonical event schema so that a session written by one framework is readable by another.

**Status: early. Spike-to-alpha.** Breaking changes expected.

The canonical event schema (schema v2) is published as [`kurrent-agent-schema`](./schema/python/) for Python and [`Kurrent.Agent.Schema`](./schema/dotnet/) for .NET; both are generated from the same Protobuf definitions in [`schema/proto/`](./schema/proto/). See [`schema/SCHEMA_v2.md`](./schema/SCHEMA_v2.md) for the field-level specification.

## Why KurrentDB for agents

Traditional agent frameworks often split message persistence, memory, observability, and evaluation across separate stores and pipelines. The same interaction data gets written multiple times, in different formats, with different failure modes.

An AI agent run is naturally a **stream of decisions**: messages received, tools called, tool results returned, responses generated, checkpoints saved, facts retained, and scores assigned. Each event already carries useful context such as content, timing, causation, and token costs. KurrentDB is purpose-built for this model.

| KurrentDB property | What it enables for agents |
|---|---|
| **Immutable append-only log** | Durable audit trail of every agent decision |
| **Catch-up subscriptions** | Memory, indexes, and analytics that build reactively from the event stream |
| **Server-side projections** | Rebuildable derived views for observability, evaluation, and reporting |
| **Temporal queries** | Ask “what did the agent know at this point?” by reading to a stream position |
| **Stream-per-entity** | Natural mapping to sessions, users, workflows, memories, and eval runs |
| **Optimistic concurrency** | Safe coordination for integrations that need contention control |

The goal is **one write, multiple capabilities, zero duplicated ingestion pipelines**: write the agent interaction once to KurrentDB, then derive history, memory, observability, and evaluation from the same event stream.

```text
Agent SDK → Canonical Events → KurrentDB Streams
                                  │
                                  ├── Chat history / resume
                                  ├── Token usage and cost analysis
                                  ├── Cross-session memory
                                  ├── Observability views
                                  ├── Evaluation datasets and scores
                                  └── Replay, audit, and compliance
```

## Implementations

Each framework integration is a standalone package with its own README, build instructions, tests, and examples.

| Framework / component | Language | Package | Status | Docs |
|---|---|---|---|---|
| Canonical schema | Python | `kurrent-agent-schema` | published on PyPI (0.4.0) | [`schema/python`](./schema/python/) |
| Canonical schema | .NET | `Kurrent.Agent.Schema` | published on NuGet (0.4.0) | [`schema/dotnet`](./schema/dotnet/) |
| Microsoft Agent Framework | .NET | `Kurrent.AgentFramework` | published on NuGet (0.4.1) | [`microsoft-agent-framework/dotnet`](./microsoft-agent-framework/dotnet/) |
| Microsoft Agent Framework | Python | `kurrent-agent-framework` | alpha (source) | [`microsoft-agent-framework/python`](./microsoft-agent-framework/python/) |
| Google ADK | Python | `kurrent-google-adk` | alpha (source) | [`google-adk/python`](./google-adk/python/) |
| Strands Agents SDK | Python | `kurrent-strands` | alpha (source) | [`strands/python`](./strands/python/) |
| OpenAI Agents SDK | Python | `kurrent-openai-agents` | alpha (source) | [`openai-agents/python`](./openai-agents/python/) |
| Claude Agent SDK | Python | `kurrent-claude-agent-sdk` | alpha (source) | [`claude-agent-sdk/python`](./claude-agent-sdk/python/) |
| Shared test utilities | Python | `kurrent-agents-testing` | internal path dependency | [`testing`](./testing/) |

## Capability overview

Capabilities vary by upstream SDK and integration maturity, but all implementations share the same canonical event vocabulary where possible.

| Integration | Session persistence | Memory | Token usage metadata | Checkpoints / workflows | Multi-agent / group chat | Evaluation |
|---|---:|---:|---:|---:|---:|---:|
| [MAF .NET](./microsoft-agent-framework/dotnet/) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| [MAF Python](./microsoft-agent-framework/python/) | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| [Google ADK](./google-adk/python/) | ✓ | ✓ | ✓ | — | framework-specific events | ✓ |
| [Strands](./strands/python/) | ✓ | ✓ | ✓ | — | extensions | — |
| [OpenAI Agents](./openai-agents/python/) | ✓ | — | ✓ | — | — | — |
| [Claude Agent SDK](./claude-agent-sdk/python/) | verbatim transcript mirror | — | decomposer shim | — | subagent streams | — |

## Canonical event schema

The canonical schema is the interoperability layer for the monorepo. Conversational events such as `UserMessageReceived`, `AssistantTextGenerated`, `AssistantToolCallsGenerated`, and `ToolResultReceived` use the same event names and payload fields across integrations. Framework-specific data lives under `extensions.<framework>` or in framework-specific event types.

Important schema docs and packages:

- [`schema/SCHEMA_v2.md`](./schema/SCHEMA_v2.md) — current schema specification.
- [`schema/proto/`](./schema/proto/) — Protobuf source used to generate shared packages.
- [`schema/python`](./schema/python/) — Python generated schema package.
- [`schema/dotnet`](./schema/dotnet/) — .NET generated schema package.
- [`schema/fixtures`](./schema/fixtures/) — JSON fixtures used for cross-language drift tests.

Most session streams use `AgentSession-{session_id}`. Memory streams use `AgentMemory-{app_name}-{user_id}`. Token usage is stored in KurrentDB event metadata under `$usage`, not in event payloads. See the schema spec for the complete stream naming, metadata, and extension conventions.

## Repository layout

| Path | Purpose |
|---|---|
| [`schema/`](./schema/) | Canonical event schema, Protobuf source, generated packages, and fixtures |
| [`google-adk/python`](./google-adk/python/) | Kurrent integration for Google ADK (Python) |
| [`microsoft-agent-framework/python`](./microsoft-agent-framework/python/) | Kurrent integration for Microsoft Agent Framework (Python) |
| [`microsoft-agent-framework/dotnet`](./microsoft-agent-framework/dotnet/) | Kurrent integration for Microsoft Agent Framework (.NET) |
| [`strands/python`](./strands/python/) | Kurrent integration for Strands Agents SDK (Python) |
| [`openai-agents/python`](./openai-agents/python/) | Kurrent integration for OpenAI Agents SDK (Python) |
| [`claude-agent-sdk/python`](./claude-agent-sdk/python/) | Kurrent SessionStore adapter for Claude Agent SDK (Python) |
| [`testing`](./testing/) | Shared pytest fixtures and Testcontainers wrapper |

## Getting started

Pick the integration for your agent framework and follow its README. Each package ships with its own `pyproject.toml`, `.slnx`, samples, or test setup as appropriate.

A running KurrentDB instance is required. Most subfolders provide a `docker-compose.yml` you can use locally:

```bash
docker compose up -d
```

## Why a monorepo

The canonical event schema is shared across every integration. Keeping everything in one repo makes drift mechanically visible: a schema change can be tested against every package at once, and shared JSON fixtures under [`schema/fixtures`](./schema/fixtures/) round-trip in every language to catch wire drift.

Per-language tooling (`uv` for Python, NuGet for .NET) coexists via path-filtered CI workflows. Each package has independent versioning and release cadence.

## License

Apache License 2.0 — see [LICENSE](./LICENSE).
