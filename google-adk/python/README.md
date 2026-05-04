# kurrent-google-adk

KurrentDB integration for [Google ADK (Python)](https://github.com/google/adk-python) — persist agent sessions, memory, artifacts, credentials, and evaluation results as events in KurrentDB.

**Status: alpha.** Session, memory, artifact, credential, and evaluation services are implemented against the canonical schema (v2). Subscriptions, projections, and ADK-side `EvalSetsManager` / `EvalSetResultsManager` are wired up.

Shares the canonical event schema with the Microsoft Agent Framework integrations (Python and .NET) — see [`schema/SCHEMA_v2.md`](../../schema/SCHEMA_v2.md) at the repo root. A session written by an ADK agent is readable by an AFW agent and vice versa.

Canonical events come from the shared `kurrent-agent-schema` Python
package. ADK-specific event types (`AgentTransferred`, `Rewind`,
`Compaction`, `StateDelta`) are added locally and serialised alongside
the canonical set. Token usage rides on the canonical `$usage` event
metadata key (shape: `kurrent_agent_schema.usage.TokenUsage`).

## Design

Full design spec: [`DESIGN.md`](./DESIGN.md).

## Install (from source)

```bash
pip install -e ".[dev]"
```

## Usage

```python
from google.adk import Agent
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from kurrent_google_adk import (
    KurrentDBSessionService,
    KurrentDBMemoryService,
    KurrentDBArtifactService,
    KurrentDBCredentialService,
    client as kdb_client,
)
from kurrent_google_adk.plugins import UsageCapturePlugin

kdb = kdb_client.from_connection_string("kurrentdb://localhost:2113?tls=false")

root_agent = Agent(name="my_agent", model="gemini-2.5-flash", instruction="...", tools=[...])

app = App(
    name="my_app",
    root_agent=root_agent,
    plugins=[UsageCapturePlugin()],
    resumability_config=ResumabilityConfig(is_resumable=True),
)

runner = Runner(
    app=app,
    session_service=KurrentDBSessionService(kdb),
    memory_service=KurrentDBMemoryService(kdb),
    artifact_service=KurrentDBArtifactService(kdb),
    credential_service=KurrentDBCredentialService(kdb),
)
```

## Run tests

```bash
pytest
```
