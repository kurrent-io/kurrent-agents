# Testcontainers Python Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `docker compose up -d` + `skip-if-unreachable` pattern in the five Python integration test suites with a shared Testcontainers-based helper so every suite starts and tears down its own KurrentDB container and is ready to run in CI unchanged.

**Architecture:** New top-level `testing/` package (`kurrent-agents-testing`) consumed by each Python package via a `uv` path dep. The package ships one `KurrentDBContainer` wrapping `testcontainers.core.container.DockerContainer`, an arch-aware image-tag resolver, and session-scoped pytest fixtures (sync + async). Each package's `conftest.py` shrinks to a fixture re-export, except `google-adk` which overrides the container fixture to request `projections=All`.

**Tech Stack:** Python 3.11+, `uv`, `testcontainers-python` (core, ≥4), `pytest`, `pytest-asyncio`, `kurrentdbclient`, Docker.

**Spec:** [`docs/superpowers/specs/2026-04-20-testcontainers-python-refactor-design.md`](../specs/2026-04-20-testcontainers-python-refactor-design.md)
**Linear:** [DEV-1483](https://linear.app/kurrent/issue/DEV-1483)
**Related:** [DEV-1492](https://linear.app/kurrent/issue/DEV-1492) (.NET parity — out of scope for this plan)

---

## File Structure

**Create:**
- `testing/pyproject.toml` — package metadata, runtime + test deps
- `testing/README.md` — one-paragraph "what is this / do not publish" note
- `testing/src/kurrent_agents_testing/__init__.py` — re-exports
- `testing/src/kurrent_agents_testing/image.py` — `resolve_image()` and `_IMAGES` dict
- `testing/src/kurrent_agents_testing/container.py` — `KurrentDBContainer` subclass of `DockerContainer`
- `testing/src/kurrent_agents_testing/fixtures.py` — `kurrentdb_container`, `kurrentdb_connection_string`, `kurrentdb_client`, `async_kurrentdb_client`, `reuse_enabled`
- `testing/tests/test_image.py` — unit tests for `resolve_image()`
- `testing/tests/test_container.py` — integration smoke test (real Docker)

**Modify:**
- `google-adk/python/pyproject.toml` — add dev dep + `[tool.uv.sources]`
- `google-adk/python/tests/conftest.py` — rewrite to override container fixture
- `google-adk/python/docker-compose.yml` — `image:` becomes env-parameterised
- `microsoft-agent-framework/python/pyproject.toml` — add dev dep + source
- `microsoft-agent-framework/python/tests/conftest.py` — rewrite
- `microsoft-agent-framework/python/docker-compose.yml` — env-parameterise image
- `strands/python/pyproject.toml` — add dev dep + source
- `strands/python/tests/conftest.py` — rewrite (sync client)
- `strands/python/docker-compose.yml` — env-parameterise image
- `openai-agents/python/pyproject.toml` — add dev dep + source
- `openai-agents/python/tests/conftest.py` — rewrite
- `openai-agents/python/docker-compose.yml` — env-parameterise image
- `claude-agent-sdk/python/pyproject.toml` — add dev dep + source
- `claude-agent-sdk/python/tests/conftest.py` — rewrite
- `claude-agent-sdk/python/docker-compose.yml` — env-parameterise image

---

## Task 1: Scaffold the `testing/` package

**Files:**
- Create: `testing/pyproject.toml`
- Create: `testing/README.md`
- Create: `testing/src/kurrent_agents_testing/__init__.py`
- Create: `testing/tests/__init__.py` (empty)
- Create: `testing/tests/test_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

Create `testing/tests/test_smoke.py`:

```python
def test_package_imports():
    import kurrent_agents_testing  # noqa: F401
```

- [ ] **Step 2: Run it — expect import error**

Run: `cd testing && uv run pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kurrent_agents_testing'` (package does not exist yet).

- [ ] **Step 3: Create the package skeleton**

Create `testing/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kurrent-agents-testing"
version = "0.0.1"
description = "Shared test helpers for the kurrent-agents repo (internal; not for publication)."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "Kurrent, Inc." }]
dependencies = [
  "testcontainers >= 4.0",
  "kurrentdbclient >= 1.2.0",
  "pytest >= 8",
  "pytest-asyncio >= 0.23",
]

[tool.hatch.build.targets.wheel]
packages = ["src/kurrent_agents_testing"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 120
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "RUF"]
```

Create `testing/README.md`:

```markdown
# kurrent-agents-testing

Shared pytest fixtures and a Testcontainers-based KurrentDB container wrapper
for the integration tests in this repo. Consumed as a path dep by each
Python package's `dev` extras. **Not published to PyPI.**

See [`docs/superpowers/specs/2026-04-20-testcontainers-python-refactor-design.md`](../docs/superpowers/specs/2026-04-20-testcontainers-python-refactor-design.md) for the design.
```

Create `testing/src/kurrent_agents_testing/__init__.py`:

```python
"""Shared test helpers for the kurrent-agents repo."""
```

Create `testing/tests/__init__.py` (empty file).

- [ ] **Step 4: Run the smoke test — expect PASS**

Run: `cd testing && uv sync && uv run pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add testing/
git commit -m "feat(testing): scaffold kurrent-agents-testing package (DEV-1483)"
```

---

## Task 2: `image.py` — image tag resolution (TDD)

**Files:**
- Create: `testing/src/kurrent_agents_testing/image.py`
- Create: `testing/tests/test_image.py`

- [ ] **Step 1: Write the failing tests**

Create `testing/tests/test_image.py`:

```python
import pytest

from kurrent_agents_testing import image as image_mod


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("KURRENTDB_IMAGE", "example.com/kurrentdb:override")
    monkeypatch.setattr(image_mod.platform, "machine", lambda: "x86_64")
    assert image_mod.resolve_image() == "example.com/kurrentdb:override"


@pytest.mark.parametrize("machine", ["arm64", "aarch64", "ARM64"])
def test_arm64_hosts(monkeypatch, machine):
    monkeypatch.delenv("KURRENTDB_IMAGE", raising=False)
    monkeypatch.setattr(image_mod.platform, "machine", lambda: machine)
    assert image_mod.resolve_image() == image_mod._IMAGES["arm64"]


@pytest.mark.parametrize("machine", ["x86_64", "amd64", "AMD64"])
def test_amd64_hosts(monkeypatch, machine):
    monkeypatch.delenv("KURRENTDB_IMAGE", raising=False)
    monkeypatch.setattr(image_mod.platform, "machine", lambda: machine)
    assert image_mod.resolve_image() == image_mod._IMAGES["amd64"]


def test_unsupported_arch_raises(monkeypatch):
    monkeypatch.delenv("KURRENTDB_IMAGE", raising=False)
    monkeypatch.setattr(image_mod.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="Unsupported CPU arch"):
        image_mod.resolve_image()


def test_image_table_has_both_arches():
    assert set(image_mod._IMAGES) == {"arm64", "amd64"}
    for tag in image_mod._IMAGES.values():
        assert tag.startswith("kurrentplatform/kurrentdb:")
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd testing && uv run pytest tests/test_image.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kurrent_agents_testing.image'`.

- [ ] **Step 3: Implement `image.py`**

Create `testing/src/kurrent_agents_testing/image.py`:

```python
"""Resolve the KurrentDB image tag for the current host architecture.

The ``kurrentplatform/kurrentdb`` experimental track does not publish a
multi-arch manifest list — separate tags exist per architecture. Callers must
either match the host CPU or set ``KURRENTDB_IMAGE`` explicitly (CI, custom
registry, pinned build).
"""

from __future__ import annotations

import os
import platform

_IMAGES: dict[str, str] = {
    "arm64": "kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble",
    "amd64": "kurrentplatform/kurrentdb:26.0.2",
}


def resolve_image() -> str:
    """Return the KurrentDB image tag to use on this host.

    Resolution order:
    1. ``KURRENTDB_IMAGE`` env var (explicit override).
    2. Arch-based lookup from :data:`_IMAGES`.
    3. ``RuntimeError`` for unsupported architectures.
    """
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

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd testing && uv run pytest tests/test_image.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add testing/src/kurrent_agents_testing/image.py testing/tests/test_image.py
git commit -m "feat(testing): arch-aware KurrentDB image tag resolver (DEV-1483)"
```

---

## Task 3: `container.py` — `KurrentDBContainer` wrapper

**Files:**
- Create: `testing/src/kurrent_agents_testing/container.py`
- Create: `testing/tests/test_container.py`

- [ ] **Step 1: Write the failing integration test**

Create `testing/tests/test_container.py`:

```python
"""Integration smoke test — requires Docker on the host.

Run individually if needed:
  uv run pytest tests/test_container.py -v -m integration
"""

import os
import socket

import pytest

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def container():
    if not _docker_available():
        pytest.skip("Docker daemon not reachable")
    from kurrent_agents_testing.container import KurrentDBContainer

    c = KurrentDBContainer().start()
    try:
        yield c
    finally:
        c.stop()


def test_container_exposes_reachable_tcp(container):
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(2113))
    with socket.create_connection((host, port), timeout=5):
        pass


def test_connection_string_is_valid_kurrentdb_uri(container):
    cs = container.connection_string()
    assert cs.startswith("kurrentdb://")
    assert "?Tls=false" in cs


def test_projections_env_propagated():
    from kurrent_agents_testing.container import KurrentDBContainer

    c = KurrentDBContainer(projections="All")
    assert c.env["KURRENTDB_RUN_PROJECTIONS"] == "All"
```

- [ ] **Step 2: Run the non-integration-only test — expect FAIL**

Run: `cd testing && uv run pytest tests/test_container.py::test_projections_env_propagated -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kurrent_agents_testing.container'`.

- [ ] **Step 3: Implement `container.py`**

Create `testing/src/kurrent_agents_testing/container.py`:

```python
"""KurrentDB Testcontainers wrapper, matching the repo's docker-compose config."""

from __future__ import annotations

import time
from urllib.error import URLError
from urllib.request import urlopen

from testcontainers.core.container import DockerContainer

from .image import resolve_image


class KurrentDBContainer(DockerContainer):
    """Start a single-node KurrentDB for tests.

    Defaults mirror the repo's docker-compose.yml: insecure mode, no
    projections, atom-pub enabled. ``google-adk`` overrides with
    ``projections="All"``.
    """

    DEFAULT_PORT = 2113

    def __init__(
        self,
        *,
        projections: str = "None",
        reuse: bool = False,
    ) -> None:
        super().__init__(resolve_image())
        (
            self.with_env("KURRENTDB_CLUSTER_SIZE", "1")
            .with_env("KURRENTDB_RUN_PROJECTIONS", projections)
            .with_env("KURRENTDB_NODE_PORT", str(self.DEFAULT_PORT))
            .with_env("KURRENTDB_INSECURE", "true")
            .with_env("KURRENTDB_ENABLE_ATOM_PUB_OVER_HTTP", "true")
            .with_exposed_ports(self.DEFAULT_PORT)
        )
        if reuse:
            # Available in testcontainers-python >= 4.
            self.with_reuse()

    def start(self):  # type: ignore[override]
        super().start()
        self._wait_for_gossip(timeout_s=120)
        return self

    def _wait_for_gossip(self, *, timeout_s: int) -> None:
        host = self.get_container_host_ip()
        port = int(self.get_exposed_port(self.DEFAULT_PORT))
        url = f"http://{host}:{port}/gossip"
        deadline = time.monotonic() + timeout_s
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urlopen(url, timeout=2) as resp:  # noqa: S310 — trusted host
                    if 200 <= resp.status < 300:
                        return
            except (URLError, ConnectionError, TimeoutError) as exc:
                last_err = exc
            time.sleep(0.5)
        raise RuntimeError(
            f"KurrentDB did not become ready within {timeout_s}s "
            f"(last error: {last_err!r})"
        )

    def connection_string(self) -> str:
        host = self.get_container_host_ip()
        port = self.get_exposed_port(self.DEFAULT_PORT)
        return f"kurrentdb://{host}:{port}?Tls=false"
```

- [ ] **Step 4: Register the `integration` marker**

Edit `testing/pyproject.toml` — inside `[tool.pytest.ini_options]`, add:

```toml
markers = [
  "integration: requires a live Docker daemon",
]
```

(The full `[tool.pytest.ini_options]` block now has `asyncio_mode`, `testpaths`, and `markers`.)

- [ ] **Step 5: Run the non-integration test — expect PASS**

Run: `cd testing && uv run pytest tests/test_container.py::test_projections_env_propagated -v`
Expected: PASS.

- [ ] **Step 6: Run the integration tests (Docker required)**

Run: `cd testing && uv run pytest tests/test_container.py -v -m integration`
Expected: all three tests PASS. The container pulls on first run (~30s); subsequent runs pull from cache.

If Docker is unreachable on the dev box, the two `integration`-marked tests will skip — that's acceptable during initial scaffolding, but this plan must be finally validated on a host with Docker running.

- [ ] **Step 7: Commit**

```bash
git add testing/src/kurrent_agents_testing/container.py testing/tests/test_container.py testing/pyproject.toml
git commit -m "feat(testing): KurrentDBContainer wrapper with /gossip wait (DEV-1483)"
```

---

## Task 4: `fixtures.py` — session-scoped pytest fixtures

**Files:**
- Create: `testing/src/kurrent_agents_testing/fixtures.py`
- Modify: `testing/src/kurrent_agents_testing/__init__.py`

- [ ] **Step 1: Implement `fixtures.py`**

Create `testing/src/kurrent_agents_testing/fixtures.py`:

```python
"""Session-scoped pytest fixtures for a KurrentDB Testcontainer.

Consumers typically import the fixtures they need into their own
``conftest.py`` via::

    from kurrent_agents_testing.fixtures import async_kurrentdb_client  # noqa: F401

The shared ``kurrentdb_container`` fixture can be overridden per package
(e.g. ``google-adk`` needs ``projections="All"``) using pytest's standard
fixture-override mechanism.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from kurrentdbclient import AsyncKurrentDBClient, KurrentDBClient

from .container import KurrentDBContainer


def reuse_enabled() -> bool:
    """``True`` outside of CI — enables testcontainers container reuse."""
    return os.environ.get("CI", "").lower() not in ("1", "true", "yes")


@pytest.fixture(scope="session")
def kurrentdb_container() -> Iterator[KurrentDBContainer]:
    c = KurrentDBContainer(reuse=reuse_enabled()).start()
    try:
        yield c
    finally:
        if not reuse_enabled():
            c.stop()


@pytest.fixture(scope="session")
def kurrentdb_connection_string(kurrentdb_container: KurrentDBContainer) -> str:
    return kurrentdb_container.connection_string()


@pytest.fixture
def kurrentdb_client(kurrentdb_connection_string: str) -> Iterator[KurrentDBClient]:
    client = KurrentDBClient(kurrentdb_connection_string)
    try:
        yield client
    finally:
        client.close()


@pytest_asyncio.fixture
async def async_kurrentdb_client(
    kurrentdb_connection_string: str,
) -> AsyncIterator[AsyncKurrentDBClient]:
    client = AsyncKurrentDBClient(kurrentdb_connection_string)
    try:
        yield client
    finally:
        # The async client lazily opens the connection on first call, so
        # close() raises when the test never used it. Mirrors the pre-existing
        # tolerance in claude-agent-sdk's conftest.
        try:
            await client.close()
        except Exception:
            pass
```

- [ ] **Step 2: Re-export from the package root**

Rewrite `testing/src/kurrent_agents_testing/__init__.py`:

```python
"""Shared test helpers for the kurrent-agents repo."""

from .container import KurrentDBContainer
from .fixtures import (
    async_kurrentdb_client,
    kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
    reuse_enabled,
)
from .image import resolve_image

__all__ = [
    "KurrentDBContainer",
    "async_kurrentdb_client",
    "kurrentdb_client",
    "kurrentdb_connection_string",
    "kurrentdb_container",
    "resolve_image",
    "reuse_enabled",
]
```

- [ ] **Step 3: Run the smoke test**

Run: `cd testing && uv run pytest tests/test_smoke.py -v`
Expected: PASS (the import path still works with the new re-exports).

- [ ] **Step 4: Commit**

```bash
git add testing/src/kurrent_agents_testing/__init__.py testing/src/kurrent_agents_testing/fixtures.py
git commit -m "feat(testing): session-scoped pytest fixtures for KurrentDB (DEV-1483)"
```

---

## Task 5: Wire up `microsoft-agent-framework/python` (canary package)

**Rationale:** This package is the simplest (purely async, no projections override) — use it as the canary. If this works, the pattern is validated and Tasks 6–9 are near-copies.

**Files:**
- Modify: `microsoft-agent-framework/python/pyproject.toml`
- Modify: `microsoft-agent-framework/python/tests/conftest.py`

- [ ] **Step 1: Update `pyproject.toml`**

Edit `microsoft-agent-framework/python/pyproject.toml` — change the `dev` extras and add a `[tool.uv.sources]` block:

```toml
[project.optional-dependencies]
dev = [
  "pytest >= 8",
  "pytest-asyncio >= 0.23",
  "ruff >= 0.6",
  "kurrent-agents-testing",
]

[tool.uv.sources]
kurrent-agents-testing = { path = "../../testing", editable = true }
```

(Append the `[tool.uv.sources]` block after the existing sections; it can sit anywhere at the top level.)

- [ ] **Step 2: Re-sync the venv**

Run: `cd microsoft-agent-framework/python && uv sync --extra dev`
Expected: resolves `kurrent-agents-testing` from the local path; no PyPI lookup.

- [ ] **Step 3: Rewrite `conftest.py`**

Replace `microsoft-agent-framework/python/tests/conftest.py` entirely with:

```python
"""Shared fixtures — delegates to kurrent-agents-testing."""

from __future__ import annotations

# Re-exported so tests referencing ``kurrentdb_client`` still work. The async
# flavour is the one this suite has always used.
from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
```

- [ ] **Step 4: Run the suite**

Run: `cd microsoft-agent-framework/python && uv run pytest -v`
Expected: every test that previously ran green (with a live DB) still passes. Startup shows a one-time image pull + container boot (~10–30s); the test body timings are unchanged.

If any test fails with "fixture not found" for a name we didn't re-export, add it to the import list above and re-run. Do **not** add helpers that don't exist in `kurrent_agents_testing` — if a test wants something new, that's a scope change for a separate ticket.

- [ ] **Step 5: Re-run with `CI=1` to exercise the no-reuse path**

Run: `cd microsoft-agent-framework/python && CI=1 uv run pytest -v`
Expected: same green result. Before/after `docker ps -a | grep kurrent` should show the container was created during the run and stopped when it finished (no leftover container with `CI=1`).

- [ ] **Step 6: Commit**

```bash
git add microsoft-agent-framework/python/pyproject.toml microsoft-agent-framework/python/tests/conftest.py
git commit -m "refactor(ms-afw-python): use Testcontainers-backed fixtures (DEV-1483)"
```

---

## Task 6: Wire up `openai-agents/python`

**Files:**
- Modify: `openai-agents/python/pyproject.toml`
- Modify: `openai-agents/python/tests/conftest.py`

- [ ] **Step 1: Update `pyproject.toml`**

Edit `openai-agents/python/pyproject.toml` — apply the same change as Task 5 Step 1 (add `kurrent-agents-testing` to `dev` extras; add the `[tool.uv.sources]` block pointing at `../../testing`).

- [ ] **Step 2: Re-sync**

Run: `cd openai-agents/python && uv sync --extra dev`

- [ ] **Step 3: Rewrite `conftest.py`**

Replace `openai-agents/python/tests/conftest.py` entirely with:

```python
"""Shared fixtures — delegates to kurrent-agents-testing."""

from __future__ import annotations

from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
```

- [ ] **Step 4: Run the suite**

Run: `cd openai-agents/python && uv run pytest -v`
Expected: all previously-green tests still pass.

- [ ] **Step 5: Commit**

```bash
git add openai-agents/python/pyproject.toml openai-agents/python/tests/conftest.py
git commit -m "refactor(openai-agents): use Testcontainers-backed fixtures (DEV-1483)"
```

---

## Task 7: Wire up `claude-agent-sdk/python`

**Files:**
- Modify: `claude-agent-sdk/python/pyproject.toml`
- Modify: `claude-agent-sdk/python/tests/conftest.py`

- [ ] **Step 1: Update `pyproject.toml`**

Same pattern as Task 5 Step 1.

- [ ] **Step 2: Re-sync**

Run: `cd claude-agent-sdk/python && uv sync --extra dev`

- [ ] **Step 3: Rewrite `conftest.py`**

Replace `claude-agent-sdk/python/tests/conftest.py` entirely with:

```python
"""Shared fixtures — delegates to kurrent-agents-testing."""

from __future__ import annotations

# The async fixture in kurrent-agents-testing already tolerates the lazy-
# connection close() behaviour this suite requires — no local shim needed.
from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
```

- [ ] **Step 4: Run the suite**

Run: `cd claude-agent-sdk/python && uv run pytest -v`
Expected: all tests pass, including those that never make a KurrentDB call (lazy-close tolerance is preserved by the shared fixture).

- [ ] **Step 5: Commit**

```bash
git add claude-agent-sdk/python/pyproject.toml claude-agent-sdk/python/tests/conftest.py
git commit -m "refactor(claude-agent-sdk): use Testcontainers-backed fixtures (DEV-1483)"
```

---

## Task 8: Wire up `strands/python` (sync client)

**Files:**
- Modify: `strands/python/pyproject.toml`
- Modify: `strands/python/tests/conftest.py`

- [ ] **Step 1: Update `pyproject.toml`**

Same pattern as Task 5 Step 1.

- [ ] **Step 2: Re-sync**

Run: `cd strands/python && uv sync --extra dev`

- [ ] **Step 3: Rewrite `conftest.py`**

Replace `strands/python/tests/conftest.py` entirely with:

```python
"""Shared fixtures — delegates to kurrent-agents-testing.

Strands' SessionManager is sync, so this suite uses the sync client fixture.
"""

from __future__ import annotations

from kurrent_agents_testing.fixtures import (  # noqa: F401
    kurrentdb_client,
    kurrentdb_connection_string,
    kurrentdb_container,
)
```

- [ ] **Step 4: Run the suite**

Run: `cd strands/python && uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add strands/python/pyproject.toml strands/python/tests/conftest.py
git commit -m "refactor(strands): use Testcontainers-backed fixtures (DEV-1483)"
```

---

## Task 9: Wire up `google-adk/python` (projections=All override)

**Files:**
- Modify: `google-adk/python/pyproject.toml`
- Modify: `google-adk/python/tests/conftest.py`

- [ ] **Step 1: Update `pyproject.toml`**

Same pattern as Task 5 Step 1.

- [ ] **Step 2: Re-sync**

Run: `cd google-adk/python && uv sync --extra dev`

- [ ] **Step 3: Rewrite `conftest.py` with fixture override**

Replace `google-adk/python/tests/conftest.py` entirely with:

```python
"""Shared fixtures — delegates to kurrent-agents-testing.

ADK needs ``projections=All`` (the OTEL projection relies on secondary indexes),
so this package overrides the shared ``kurrentdb_container`` fixture with
pytest's standard name-based override mechanism.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kurrent_agents_testing.container import KurrentDBContainer
from kurrent_agents_testing.fixtures import (  # noqa: F401
    async_kurrentdb_client as kurrentdb_client,
    kurrentdb_connection_string,
    reuse_enabled,
)


@pytest.fixture(scope="session")
def kurrentdb_container() -> Iterator[KurrentDBContainer]:
    c = KurrentDBContainer(projections="All", reuse=reuse_enabled()).start()
    try:
        yield c
    finally:
        if not reuse_enabled():
            c.stop()
```

- [ ] **Step 4: Run the suite**

Run: `cd google-adk/python && uv run pytest -v`
Expected: all tests pass. Confirm the override took effect by checking that the container's env shows `KURRENTDB_RUN_PROJECTIONS=All` — `docker inspect $(docker ps -q -f ancestor=kurrentplatform/kurrentdb) | grep RUN_PROJECTIONS` should show `All`, not `None`.

- [ ] **Step 5: Commit**

```bash
git add google-adk/python/pyproject.toml google-adk/python/tests/conftest.py
git commit -m "refactor(google-adk): use Testcontainers fixtures with projections=All (DEV-1483)"
```

---

## Task 10: Parameterise docker-compose image line (dev-loop only)

**Scope:** The compose files stay for sample apps. This step makes their image line overridable so an amd64 dev box isn't forced to edit the file. Tests no longer touch these files.

**Files:**
- Modify: `google-adk/python/docker-compose.yml`
- Modify: `microsoft-agent-framework/python/docker-compose.yml`
- Modify: `strands/python/docker-compose.yml`
- Modify: `openai-agents/python/docker-compose.yml`
- Modify: `claude-agent-sdk/python/docker-compose.yml`

- [ ] **Step 1: Update each compose file's `image:` line**

In each of the five files, change:

```yaml
    image: kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble
```

to:

```yaml
    image: ${KURRENTDB_IMAGE:-kurrentplatform/kurrentdb:26.0.2-experimental-arm64-10.0-noble}
```

No other changes. Do **not** edit the `.NET` compose file — that's covered by DEV-1492.

- [ ] **Step 2: Validate compose still parses**

Run (for each file):

```bash
docker compose -f google-adk/python/docker-compose.yml config > /dev/null
docker compose -f microsoft-agent-framework/python/docker-compose.yml config > /dev/null
docker compose -f strands/python/docker-compose.yml config > /dev/null
docker compose -f openai-agents/python/docker-compose.yml config > /dev/null
docker compose -f claude-agent-sdk/python/docker-compose.yml config > /dev/null
```

Expected: no output, exit code 0 for each.

- [ ] **Step 3: Spot-check env substitution**

Run:

```bash
KURRENTDB_IMAGE=example.com/kdb:test docker compose -f strands/python/docker-compose.yml config | grep image:
```

Expected: `image: example.com/kdb:test`.

- [ ] **Step 4: Commit**

```bash
git add google-adk/python/docker-compose.yml microsoft-agent-framework/python/docker-compose.yml strands/python/docker-compose.yml openai-agents/python/docker-compose.yml claude-agent-sdk/python/docker-compose.yml
git commit -m "refactor(compose): parameterise KurrentDB image for amd64 dev boxes (DEV-1483)"
```

---

## Task 11: Full-repo validation sweep

**Files:** none modified (read-only validation).

- [ ] **Step 1: Green-run every Python suite end-to-end**

Run, in a fresh shell (no reused container):

```bash
for pkg in google-adk/python microsoft-agent-framework/python strands/python openai-agents/python claude-agent-sdk/python; do
  echo "=== $pkg ==="
  (cd "$pkg" && uv run pytest -v) || { echo "FAIL in $pkg"; exit 1; }
done
```

Expected: every suite green.

- [ ] **Step 2: Simulate CI (`CI=1`) for one suite**

Run:

```bash
cd microsoft-agent-framework/python && CI=1 uv run pytest -v
docker ps -a --filter "label=org.testcontainers.reuse=true" --format '{{.Names}}'
```

Expected: tests pass; the `docker ps` command returns no rows (reuse labels were never written because reuse was disabled).

- [ ] **Step 3: Force the amd64 tag from an arm64 host (or vice versa)**

Run:

```bash
cd microsoft-agent-framework/python && KURRENTDB_IMAGE=kurrentplatform/kurrentdb:26.0.2 uv run pytest -v
```

Expected: on an arm64 host, Docker Desktop emulates amd64 and the suite runs (slower). This confirms the env-var override path.

If this step fails because the image doesn't exist or won't pull, flag in the PR description — it means the amd64 tag assumption needs revisiting (this risk is already noted in the spec).

- [ ] **Step 4: Confirm the old conftest helpers are gone**

Run (from repo root):

```bash
grep -RE "_kurrentdb_reachable|kurrentdb_available|_DEFAULT_CONNECTION_STRING" \
  --include='*.py' google-adk microsoft-agent-framework strands openai-agents claude-agent-sdk
```

Expected: no output. If any hit appears outside `.venv` directories, delete the dead code before finishing.

- [ ] **Step 5: Confirm no test uses `pytest.skip("KurrentDB unreachable")` anymore**

Run:

```bash
grep -R "KurrentDB unreachable" --include='*.py' . || echo "no matches (expected)"
```

Expected: `no matches (expected)`.

- [ ] **Step 6: Final commit (only if anything surfaced)**

If steps 4 or 5 surface dead code, remove it and commit:

```bash
git add -p   # review chunk-by-chunk
git commit -m "chore: remove dead conftest helpers after DEV-1483"
```

Otherwise this task is fully checklist-only — no commit.

---

## Self-Review Notes

**Spec coverage** (cross-reference against `2026-04-20-testcontainers-python-refactor-design.md`):

- Architecture / `testing/` package layout → Task 1
- `image.py` with arch dict + env override → Task 2
- `KurrentDBContainer` with `/gossip` wait → Task 3
- session-scoped fixtures, `reuse_enabled()`, CI detection → Task 4
- Per-package conftest rewrite (five packages) → Tasks 5–9
- google-adk projections=All via fixture override → Task 9
- docker-compose image parameterisation → Task 10
- Validation plan (local, forced amd64, CI simulation, `resolve_image()` unit tests) → Task 2 (unit), Task 3 (integration), Task 5 Step 5 (CI sim), Task 11 Step 3 (amd64 override)
- Non-goals (CI YAML, .NET fixture, compose removal) → explicitly excluded above

All spec requirements map to at least one task. No gaps.

**Placeholder scan:** no TBDs, no "implement appropriate error handling", no "similar to Task N" shortcuts — all tasks carry their own complete code blocks.

**Type consistency:** `KurrentDBContainer`, `resolve_image`, `reuse_enabled`, `kurrentdb_container`, `kurrentdb_connection_string`, `kurrentdb_client`, `async_kurrentdb_client` — names match across Tasks 2–9 and the `__init__.py` re-exports in Task 4.
