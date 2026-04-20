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
