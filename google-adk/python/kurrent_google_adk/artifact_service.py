"""``KurrentDBArtifactService`` — see ``DESIGN.md`` §7.3.

One KurrentDB stream per artifact file, carrying one canonical
``ArtifactVersionCreated`` event per saved version. Version number equals
stream position: first save returns ``0``, second returns ``1``, and so on —
matching the ``BaseArtifactService`` contract.

**v1 scope.**

- Inline storage only (``types.Part.inline_data``, ``text``) up to
  ``inline_threshold_bytes`` (default 1 MiB). Larger payloads raise
  :class:`ArtifactTooLargeError`. Pluggable ``BlobSink`` for external offload
  is a follow-up (``DESIGN.md`` §13 open question 1).
- ``types.Part.file_data`` is stored as a reference via the event's
  ``canonical_uri``; the actual bytes are assumed to live in external storage
  that the caller manages (matches the base class docstring).
- ``list_artifact_keys`` reads ``$ce-AgentArtifact`` with resolved links and
  scopes by ``(app_name, user_id, session_id)`` pulled from per-event
  metadata. Requires the KurrentDB category projection (enabled in the
  bundled ``docker-compose.yml``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from google.adk.artifacts.base_artifact_service import (
    ArtifactVersion,
    BaseArtifactService,
    ensure_part,
)
from google.genai import types
from kurrentdbclient import StreamState
from kurrentdbclient.exceptions import NotFoundError, StreamIsDeletedError

from . import _serialization
from ._schema import events as _events
from ._schema.stream_names import for_artifact

if TYPE_CHECKING:  # pragma: no cover
    from kurrentdbclient import AsyncKurrentDBClient, RecordedEvent


# 1 MiB — payloads over this bounce to BlobSink (follow-up) or raise in v1.
DEFAULT_INLINE_THRESHOLD_BYTES = 1 << 20

# Event-metadata key carrying the artifact's scope. Enables cross-stream
# enumeration in ``list_artifact_keys`` without parsing stream names.
_SCOPE_METADATA_KEY = "$scope"

# Hint (in ``custom_metadata``) describing which Part field the data came
# from, so we can reconstruct the same shape on ``load_artifact``.
_PART_KIND_KEY = "__part_kind__"
_PART_KIND_TEXT = "text"
_PART_KIND_INLINE = "inline_data"
_PART_KIND_FILE = "file_data"

# Category stream used by ``list_artifact_keys``. Populated automatically by
# KurrentDB when ``KURRENTDB_RUN_PROJECTIONS`` enables the category projection.
_CATEGORY_STREAM = "$ce-AgentArtifact"


class ArtifactTooLargeError(ValueError):
    """Raised when an inline artifact payload exceeds the configured threshold."""


class KurrentDBArtifactService(BaseArtifactService):
    """KurrentDB-backed artifact service with optional blob offload (v1: inline only)."""

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        inline_threshold_bytes: int = DEFAULT_INLINE_THRESHOLD_BYTES,
    ) -> None:
        self._client = client
        self._inline_threshold_bytes = inline_threshold_bytes

    # ----- writes ------------------------------------------------------------

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
        part = ensure_part(artifact)
        stream = for_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        next_version = await self._next_version(stream)

        payload = _encode_part(part)
        if (
            payload.inline_bytes is not None
            and len(payload.inline_bytes) > self._inline_threshold_bytes
        ):
            raise ArtifactTooLargeError(
                f"Artifact {filename!r} payload ({len(payload.inline_bytes)} bytes) "
                f"exceeds inline threshold ({self._inline_threshold_bytes} bytes). "
                "Pluggable blob offload is not implemented in v1."
            )

        merged_metadata: dict[str, Any] = {_PART_KIND_KEY: payload.kind}
        if custom_metadata:
            merged_metadata.update(custom_metadata)

        event = _events.ArtifactVersionCreated(
            version=next_version,
            mime_type=payload.mime_type,
            inline_bytes=payload.inline_bytes,
            canonical_uri=(
                payload.canonical_uri
                if payload.canonical_uri is not None
                else _synthesize_stream_uri(stream, next_version)
            ),
            custom_metadata=merged_metadata,
            created_at=datetime.now(UTC),
        )

        scope_metadata = {
            _SCOPE_METADATA_KEY: {
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "filename": filename,
            }
        }

        await self._client.append_to_stream(
            stream,
            events=[_serialization.serialize(event, metadata=scope_metadata)],
            current_version=StreamState.ANY,
        )
        return next_version

    # ----- reads -------------------------------------------------------------

    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
        version: int | None = None,
    ) -> types.Part | None:
        event = await self._read_event(
            app_name=app_name,
            user_id=user_id,
            filename=filename,
            session_id=session_id,
            version=version,
        )
        if event is None:
            return None
        return _decode_part(event)

    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> list[int]:
        events = await self._read_all_events(
            app_name=app_name,
            user_id=user_id,
            filename=filename,
            session_id=session_id,
        )
        return [event.version for event in events]

    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> list[ArtifactVersion]:
        stream = for_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        events = await self._read_all_events(
            app_name=app_name,
            user_id=user_id,
            filename=filename,
            session_id=session_id,
        )
        return [_to_artifact_version(event, stream=stream) for event in events]

    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
        version: int | None = None,
    ) -> ArtifactVersion | None:
        stream = for_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        event = await self._read_event(
            app_name=app_name,
            user_id=user_id,
            filename=filename,
            session_id=session_id,
            version=version,
        )
        if event is None:
            return None
        return _to_artifact_version(event, stream=stream)

    # ----- delete ------------------------------------------------------------

    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> None:
        stream = for_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        try:
            await self._client.delete_stream(stream, current_version=StreamState.ANY)
        except (NotFoundError, StreamIsDeletedError):
            # Idempotent: deleting a missing artifact is not an error.
            pass

    # ----- list keys ---------------------------------------------------------

    async def list_artifact_keys(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str | None = None,
    ) -> list[str]:
        """Scan the ``$ce-AgentArtifact`` category projection for in-scope filenames.

        Requires the KurrentDB category projection to be enabled. Returns user-
        scoped keys when ``session_id`` is ``None``; otherwise returns both
        session-scoped keys for that session and user-scoped keys.
        """
        keys: set[str] = set()
        try:
            response = await self._client.read_stream(
                _CATEGORY_STREAM, resolve_links=True
            )
        except NotFoundError:
            return []

        async for recorded in response:
            metadata = _serialization.read_metadata(recorded)
            if not metadata:
                continue
            scope = metadata.get(_SCOPE_METADATA_KEY)
            if not scope:
                continue
            if scope.get("app_name") != app_name or scope.get("user_id") != user_id:
                continue
            event_session = scope.get("session_id")
            if session_id is None:
                if event_session is not None:
                    continue
            else:
                if event_session is not None and event_session != session_id:
                    continue
            filename = scope.get("filename")
            if filename:
                keys.add(filename)
        return sorted(keys)

    # ----- internals ---------------------------------------------------------

    async def _next_version(self, stream: str) -> int:
        try:
            tail = await self._client.get_stream(stream, backwards=True, limit=1)
        except (NotFoundError, StreamIsDeletedError):
            return 0
        tail_list = list(tail)
        if not tail_list:
            return 0
        return tail_list[0].stream_position + 1

    async def _read_event(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None,
        version: int | None,
    ) -> _events.ArtifactVersionCreated | None:
        stream = for_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        try:
            if version is None:
                records = await self._client.get_stream(
                    stream, backwards=True, limit=1
                )
            else:
                records = await self._client.get_stream(
                    stream, stream_position=version, limit=1
                )
        except (NotFoundError, StreamIsDeletedError):
            return None
        records_list = list(records)
        if not records_list:
            return None
        event = _serialization.deserialize(records_list[0])
        if not isinstance(event, _events.ArtifactVersionCreated):
            return None
        return event

    async def _read_all_events(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None,
    ) -> list[_events.ArtifactVersionCreated]:
        stream = for_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        try:
            records = await self._client.get_stream(stream)
        except (NotFoundError, StreamIsDeletedError):
            return []
        result: list[_events.ArtifactVersionCreated] = []
        for recorded in records:
            event = _serialization.deserialize(recorded)
            if isinstance(event, _events.ArtifactVersionCreated):
                result.append(event)
        return result


# ----- Part encode / decode --------------------------------------------------


class _Payload:
    __slots__ = ("inline_bytes", "canonical_uri", "mime_type", "kind")

    def __init__(
        self,
        *,
        inline_bytes: bytes | None,
        canonical_uri: str | None,
        mime_type: str | None,
        kind: str,
    ) -> None:
        self.inline_bytes = inline_bytes
        self.canonical_uri = canonical_uri
        self.mime_type = mime_type
        self.kind = kind


def _encode_part(part: types.Part) -> _Payload:
    if part.inline_data is not None:
        return _Payload(
            inline_bytes=part.inline_data.data,
            canonical_uri=None,
            mime_type=part.inline_data.mime_type,
            kind=_PART_KIND_INLINE,
        )
    if part.file_data is not None:
        return _Payload(
            inline_bytes=None,
            canonical_uri=part.file_data.file_uri,
            mime_type=part.file_data.mime_type,
            kind=_PART_KIND_FILE,
        )
    if part.text is not None:
        return _Payload(
            inline_bytes=part.text.encode("utf-8"),
            canonical_uri=None,
            mime_type="text/plain; charset=utf-8",
            kind=_PART_KIND_TEXT,
        )
    raise ValueError(
        "Unsupported artifact Part: expected inline_data, file_data, or text. "
        "Other Part kinds (function_call, code_execution_result, etc.) are not "
        "artifact content."
    )


def _decode_part(event: _events.ArtifactVersionCreated) -> types.Part:
    kind = (event.custom_metadata or {}).get(_PART_KIND_KEY)
    if kind == _PART_KIND_TEXT and event.inline_bytes is not None:
        return types.Part(text=event.inline_bytes.decode("utf-8"))
    if kind == _PART_KIND_FILE and event.canonical_uri is not None:
        return types.Part(
            file_data=types.FileData(
                file_uri=event.canonical_uri,
                mime_type=event.mime_type,
            )
        )
    # Default / inline_data (kind may be missing in legacy events).
    if event.inline_bytes is not None:
        return types.Part(
            inline_data=types.Blob(
                data=event.inline_bytes, mime_type=event.mime_type
            )
        )
    if event.canonical_uri is not None:
        # Event only has a URI but wasn't marked as file_data — treat as file_data.
        return types.Part(
            file_data=types.FileData(
                file_uri=event.canonical_uri,
                mime_type=event.mime_type,
            )
        )
    # Nothing to decode — unusual but not fatal.
    return types.Part()


# ----- helpers ---------------------------------------------------------------


def _synthesize_stream_uri(stream: str, version: int) -> str:
    """Opaque URI used when an artifact is stored inline.

    Lets us satisfy the ``ArtifactVersion.canonical_uri`` required field
    without forcing every caller to provide an external URI. The format is
    readable by our own loader via ``(stream, version)`` decomposition, and
    opaque to anyone else.
    """
    return f"kurrentdb+stream://{stream}/{version}"


def _to_artifact_version(
    event: _events.ArtifactVersionCreated, *, stream: str
) -> ArtifactVersion:
    return ArtifactVersion(
        version=event.version,
        canonical_uri=(
            event.canonical_uri
            if event.canonical_uri is not None
            else _synthesize_stream_uri(stream, event.version)
        ),
        custom_metadata=dict(event.custom_metadata or {}),
        create_time=event.created_at.timestamp(),
        mime_type=event.mime_type,
    )
