"""``KurrentDBArtifactService`` — see ``DESIGN.md`` §7.3.

Implements ``google.adk.artifacts.BaseArtifactService`` with one KurrentDB
stream per artifact. Each ``save_artifact`` call emits an
``ArtifactVersionCreated`` event; version equals stream revision. Payloads
above a configurable threshold are offloaded via a pluggable ``BlobSink``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.artifacts.base_artifact_service import ArtifactVersion, BaseArtifactService

if TYPE_CHECKING:  # pragma: no cover
    from google.genai import types
    from kurrentdbclient import AsyncKurrentDBClient


# Default inline threshold; configurable per-instance.
DEFAULT_INLINE_THRESHOLD_BYTES = 1 << 20  # 1 MiB


class KurrentDBArtifactService(BaseArtifactService):
    """KurrentDB-backed artifact service with optional blob offload."""

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        inline_threshold_bytes: int = DEFAULT_INLINE_THRESHOLD_BYTES,
    ) -> None:
        self._client = client
        self._inline_threshold_bytes = inline_threshold_bytes

    async def save_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        artifact: types.Part | dict[str, Any],
        session_id: str | None = None,
        custom_metadata: dict[str, Any] | None = None,
    ) -> int:
        raise NotImplementedError

    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
        version: int | None = None,
    ) -> types.Part | None:
        raise NotImplementedError

    async def list_artifact_keys(
        self, *, app_name: str, user_id: str, session_id: str | None = None
    ) -> list[str]:
        raise NotImplementedError

    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> list[int]:
        raise NotImplementedError

    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> list[ArtifactVersion]:
        raise NotImplementedError

    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
        version: int | None = None,
    ) -> ArtifactVersion | None:
        raise NotImplementedError
