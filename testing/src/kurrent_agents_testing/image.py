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
