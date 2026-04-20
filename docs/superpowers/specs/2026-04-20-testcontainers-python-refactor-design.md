# DEV-1483 — Replace docker-compose with Testcontainers in Python tests

Linear: [DEV-1483](https://linear.app/kurrent/issue/DEV-1483/refactor-tests-to-use-testcontainers-instead-of-relying-on-compose)
Related: [DEV-1492](https://linear.app/kurrent/issue/DEV-1492/fix-net-kurrentdbfixture-to-pick-kurrentdb-image-tag-per-host) (.NET fixture parity — separate ticket)

## Problem

All five Python integration suites (`google-adk`, `microsoft-agent-framework/python`, `strands`, `openai-agents`, `claude-agent-sdk`) depend on a docker-compose file that the developer must start manually. Each `conftest.py` socket-probes `localhost:2113` and calls `pytest.skip()` when the DB is unreachable. That pattern:

- Will not survive CI: skipped tests aren't a failure mode we want in a regression signal.
- Silently hides outages when a local container dies mid-run.
- Duplicates near-identical probe / fixture code across five packages.

The .NET integration tests already use `Testcontainers.KurrentDb` and are the target shape.

## Goal

Make every Python suite self-contained: `pytest` alone (plus a running Docker daemon) starts the container it needs, tears it down afterwards, and is ready for CI without further plumbing. The existing `docker-compose.yml` files stay, but only as a dev-loop/demo convenience for sample apps — the tests no longer depend on them.

**Non-goals**

- Writing the CI workflow YAML. This spec prepares the tests for CI; the workflow is a follow-up.
- Fixing the .NET fixture's arch-tag hard-code (tracked in DEV-1492).
- Removing docker-compose files.

## Architecture

New top-level `testing/` directory containing a tiny, never-published Python package (`kurrent-agents-testing`) consumed as a path-dep by every Python package's dev extras.

```
testing/
  pyproject.toml                    # name = "kurrent-agents-testing"
  src/kurrent_agents_testing/
    __init__.py
    image.py                        # tag selection (arch dict + env override)
    container.py                    # KurrentDBContainer (subclass of DockerContainer)
    fixtures.py                     # pytest fixtures (sync + async)
```

Each consumer package's `pyproject.toml` adds:

```toml
[tool.uv.sources]
kurrent-agents-testing = { path = "../../testing", editable = true }
```

(All five Python packages already use `uv`, confirmed by the `.venv` layout.)

### Why a shared module (and not a pip-installable package or copy-paste)

- **Not copy-paste** — five near-identical conftests rot independently; bumping the image tag is currently a five-file edit and will get worse.
- **Not PyPI-published** — this is test infrastructure, not an artifact for users. A path-dep keeps it editable and out of the public API of any integration.
- **Not a git submodule** — the Python packages live alongside it in the same repo; no benefit.

## Components

### `image.py` — image tag selection

```python
import os, platform

_IMAGES = {
    "arm64": "kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble",
    "amd64": "kurrentplatform/kurrentdb:26.0.2",
}

def resolve_image() -> str:
    override = os.environ.get("KURRENTDB_IMAGE")
    if override:
        return override
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return _IMAGES["arm64"]
    if machine in ("x86_64", "amd64"):
        return _IMAGES["amd64"]
    raise RuntimeError(f"Unsupported CPU arch for KurrentDB image: {machine!r}")
```

Resolution order: explicit `KURRENTDB_IMAGE` env var → arch-based pick → hard fail. No silent default — pulling the wrong-arch image is a confusing failure mode we'd rather surface up front.

**Rationale.** The `kurrentplatform/kurrentdb` experimental track does not ship a multi-arch manifest list; each arch has a distinct tag. The two tags above are what we verified: the arm64 "experimental-arm64-10.0-noble" build carries the secondary-index feature the OTEL projection in the .NET fixture relied on, and `26.0.2` is the amd64 tag confirmed to work for our test scope.

### `container.py` — container wrapper

```python
from testcontainers.core.container import DockerContainer
from .image import resolve_image

class KurrentDBContainer(DockerContainer):
    """Thin wrapper around DockerContainer — matches the docker-compose config."""

    def __init__(self, *, projections: str = "None", reuse: bool = False):
        super().__init__(resolve_image())
        (self.with_env("KURRENTDB_CLUSTER_SIZE", "1")
             .with_env("KURRENTDB_RUN_PROJECTIONS", projections)
             .with_env("KURRENTDB_NODE_PORT", "2113")
             .with_env("KURRENTDB_INSECURE", "true")
             .with_env("KURRENTDB_ENABLE_ATOM_PUB_OVER_HTTP", "true")
             .with_exposed_ports(2113))
        if reuse:
            # testcontainers-python ≥ 4 ships `.with_reuse()`; label-based
            # fallback kept in mind if a pinned older version lacks it.
            self.with_reuse()

    def start(self):
        super().start()
        _wait_http_ok(
            host=self.get_container_host_ip(),
            port=int(self.get_exposed_port(2113)),
            path="/gossip",
            timeout=120,
        )
        return self

    def connection_string(self) -> str:
        host = self.get_container_host_ip()
        port = self.get_exposed_port(2113)
        return f"kurrentdb://{host}:{port}?Tls=false"
```

`_wait_http_ok` polls `http://<host>:<mapped-port>/gossip` until a 2xx response or timeout. This mirrors the .NET fixture's `Wait.ForUnixContainer().UntilHttpRequestIsSucceeded(x => x.ForPath("/gossip").ForPort(2113))` so both language test suites use the same readiness signal.

**Projections.** The default is ``RUN_PROJECTIONS=System`` plus ``START_STANDARD_PROJECTIONS=true`` — this is enough to enable ``$ce-*``, ``$by_category``, and the other built-in system projections that every integration suite (including ADK's category-stream usage) depends on. Callers that want something stricter can pass ``projections="None"``; ``"All"`` remains available for users-defined projection needs. No per-package override is required today.

### `fixtures.py` — pytest fixtures

```python
import os, pytest, pytest_asyncio
from kurrentdbclient import KurrentDBClient, AsyncKurrentDBClient
from .container import KurrentDBContainer

def reuse_enabled() -> bool:
    """Public helper — packages overriding kurrentdb_container should use it."""
    return os.environ.get("CI", "").lower() not in ("1", "true")

@pytest.fixture(scope="session")
def kurrentdb_container():
    c = KurrentDBContainer(reuse=reuse_enabled()).start()
    yield c
    if not reuse_enabled():
        c.stop()

@pytest.fixture(scope="session")
def kurrentdb_connection_string(kurrentdb_container) -> str:
    return kurrentdb_container.connection_string()

@pytest.fixture
def kurrentdb_client(kurrentdb_connection_string):
    client = KurrentDBClient(kurrentdb_connection_string)
    try:
        yield client
    finally:
        client.close()

@pytest_asyncio.fixture
async def async_kurrentdb_client(kurrentdb_connection_string):
    client = AsyncKurrentDBClient(kurrentdb_connection_string)
    try:
        yield client
    finally:
        # Lazy-created connection — close() raises on a never-used client.
        # Mirror claude-agent-sdk's existing tolerance.
        try:
            await client.close()
        except Exception:
            pass
```

**Container lifetime.** Session-scoped container, one per `pytest` invocation. `with_reuse()` labels the container when not in CI so successive local `pytest` runs attach to the same container — sub-second startup on the second run. CI detection is simply `CI` env var (set by GitHub Actions, GitLab, CircleCI, etc.); reuse is disabled there so every job starts fresh.

**No unreachable-skip path.** Docker is a hard prerequisite for running the test suites. Failure to start the container is a test failure, not a skip.

## Per-package changes

Each package's `conftest.py` collapses to:

```python
# strands/python/tests/conftest.py  — sync
from kurrent_agents_testing.fixtures import kurrentdb_client  # noqa: F401
```

```python
# The four async packages — rebind to the existing fixture name each test uses
from kurrent_agents_testing.fixtures import async_kurrentdb_client as kurrentdb_client  # noqa: F401
```

All five packages — including `google-adk` — use the same 4-line re-export because the shared default (`RUN_PROJECTIONS=System`) already covers ADK's category-stream needs. If a future package needs a non-default projections value, it can use pytest's fixture-override mechanism: define a local `kurrentdb_container` fixture in its `conftest.py` that constructs a `KurrentDBContainer(projections=...)` with the desired setting. Pytest's name-based fixture lookup picks up the local definition and uses it everywhere the shared fixture would have been resolved.

The old `_connection_string`, `_kurrentdb_reachable`, and `kurrentdb_available` helpers (plus the module-level `_DEFAULT_CONNECTION_STRING` constant) are deleted. No other code references them (verified: `Grep` across all 5 tests trees shows fixture-only usage).

## Compose files

Kept for sample apps and manual dev loops. Each Python `docker-compose.yml` has the image line updated so amd64 dev machines can override without editing the file:

```yaml
image: ${KURRENTDB_IMAGE:-kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble}
```

The .NET `docker-compose.yml` is covered by DEV-1492 (same hard-coded tag issue).

## Dependencies

Add to `testing/pyproject.toml`:

- `testcontainers` (core only; we do not use any provider-specific submodule — no `testcontainers-kurrentdb` Python package exists today).
- `pytest`, `pytest-asyncio` — for the fixture definitions.
- `kurrentdbclient` — for the client fixtures.

Each consumer package's existing dev-dep on `pytest`/`pytest-asyncio`/`kurrentdbclient` stays; the new `kurrent-agents-testing` path-dep joins them.

## Validation plan

- **Local, arm64.** Run each of the five packages' `pytest` suite. All green, container pulls once per session, reused across runs.
- **Local, forced amd64.** `KURRENTDB_IMAGE=kurrentplatform/kurrentdb:26.0.2 pytest` on at least one package. Confirms the amd64 tag line works (even from an arm64 host, Docker Desktop will emulate).
- **CI simulation.** `CI=1 pytest` — confirms `with_reuse()` is disabled and the container is stopped at session end (use `docker ps -a` before/after to verify).
- **`resolve_image()` unit tests.** Monkeypatch `platform.machine()` and `os.environ["KURRENTDB_IMAGE"]` across the three branches (arm64, amd64, override) plus the unsupported-arch error path.

## Risks / open questions

- **`with_reuse()` on Docker Desktop for Mac** occasionally leaves orphaned containers when the daemon is restarted mid-session. If that bites, we add a one-line teardown hook (`pytest_sessionfinish`) that force-stops any container labelled with the reuse key on specific opt-in. Not worth pre-building.
- **`26.0.2` amd64 tag parity with `26.0.2-experimental-arm64-10.0-noble`.** Trusting the user's confirmation that `26.0.2` works; if a test leans on the experimental secondary-index feature on amd64, we'd need a matching `-experimental-x64-...` tag (to be determined at implementation time, not now).

## Out of scope (explicit)

- GitHub Actions / other CI workflow YAML.
- .NET fixture arch-tag parity (DEV-1492).
- Removing any docker-compose file.
- Sharing test code or fixtures with the .NET side.
