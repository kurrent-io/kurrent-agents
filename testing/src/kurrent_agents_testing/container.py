"""KurrentDB Testcontainers wrapper, matching the repo's docker-compose config."""

from __future__ import annotations

import time
from urllib.error import URLError
from urllib.request import urlopen

from testcontainers.core.container import DockerContainer

from .image import resolve_image


class KurrentDBContainer(DockerContainer):
    """Start a single-node KurrentDB for tests.

    Defaults to ``RUN_PROJECTIONS=System`` + ``START_STANDARD_PROJECTIONS=true``
    — enough for ``$ce-*`` and the other built-in projections the integration
    suites rely on. Callers that need something stricter or looser (``All`` /
    ``None``) can override via the ``projections`` kwarg.
    """

    DEFAULT_PORT = 2113

    def __init__(
        self,
        *,
        projections: str = "System",
        reuse: bool = False,
    ) -> None:
        super().__init__(resolve_image())
        (
            self.with_env("KURRENTDB_CLUSTER_SIZE", "1")
            .with_env("KURRENTDB_RUN_PROJECTIONS", projections)
            # $ce-* and the other built-in "system" projections (by-category,
            # by-event-type, stream-by-category, event-by-type) are required
            # by our integration tests and by the per-package sample apps.
            # Always auto-start them; callers that genuinely need a minimal
            # server can pass ``projections="None"`` explicitly.
            .with_env("KURRENTDB_START_STANDARD_PROJECTIONS", "true")
            .with_env("KURRENTDB_NODE_PORT", str(self.DEFAULT_PORT))
            .with_env("KURRENTDB_INSECURE", "true")
            .with_env("KURRENTDB_ENABLE_ATOM_PUB_OVER_HTTP", "true")
            .with_exposed_ports(self.DEFAULT_PORT)
        )
        self._reuse_applied = False
        if reuse and hasattr(self, "with_reuse"):
            # ``with_reuse()`` is only present in newer testcontainers-python
            # (not in 4.14.2, the version pinned by uv.lock). When absent we
            # silently fall back to "no reuse"; teardown in fixtures.py keys off
            # ``_reuse_applied`` so the container is always stopped cleanly
            # instead of orphaned.
            self.with_reuse()
            self._reuse_applied = True

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
                with urlopen(url, timeout=2) as resp:  # trusted: local Docker container
                    if 200 <= resp.status < 300:
                        return
            except (URLError, ConnectionError, TimeoutError) as exc:
                last_err = exc
            time.sleep(0.5)
        container_status = getattr(self._container, "status", "unknown")
        raise RuntimeError(
            f"KurrentDB did not become ready within {timeout_s}s "
            f"(container status: {container_status}, last error: {last_err!r})"
        )

    def connection_string(self) -> str:
        host = self.get_container_host_ip()
        port = self.get_exposed_port(self.DEFAULT_PORT)
        return f"kurrentdb://{host}:{port}?Tls=false"
