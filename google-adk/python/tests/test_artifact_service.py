"""Integration tests for ``KurrentDBArtifactService``."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from google.genai import types
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_google_adk import KurrentDBArtifactService
from kurrent_google_adk.artifact_service import (
    DEFAULT_INLINE_THRESHOLD_BYTES,
    ArtifactTooLargeError,
)


def _ids() -> tuple[str, str, str]:
    """Return isolated (app_name, user_id, session_id) for one test."""
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}", f"session_{suffix}")


def _text_part(text: str) -> types.Part:
    return types.Part(text=text)


def _inline_part(data: bytes, mime_type: str = "application/octet-stream") -> types.Part:
    return types.Part(inline_data=types.Blob(data=data, mime_type=mime_type))


def _file_part(uri: str, mime_type: str = "image/png") -> types.Part:
    return types.Part(file_data=types.FileData(file_uri=uri, mime_type=mime_type))


class TestSaveLoad:
    async def test_round_trip_text_part(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()

        v0 = await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="notes.txt",
            artifact=_text_part("hello world"),
        )
        assert v0 == 0

        part = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="notes.txt"
        )
        assert part is not None
        assert part.text == "hello world"

    async def test_round_trip_inline_data(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        data = b"\x89PNG\r\n\x1a\n...fake-png-bytes..."

        await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="logo.png",
            artifact=_inline_part(data, "image/png"),
        )
        part = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="logo.png"
        )
        assert part is not None and part.inline_data is not None
        assert part.inline_data.data == data
        assert part.inline_data.mime_type == "image/png"

    async def test_round_trip_file_data(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        uri = "gs://example-bucket/video.mp4"

        await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="video.mp4",
            artifact=_file_part(uri, "video/mp4"),
        )
        part = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="video.mp4"
        )
        assert part is not None and part.file_data is not None
        assert part.file_data.file_uri == uri
        assert part.file_data.mime_type == "video/mp4"

    async def test_missing_artifact_returns_none(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        part = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="absent.txt"
        )
        assert part is None

    async def test_oversize_inline_raises(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(
            kurrentdb_client, inline_threshold_bytes=128
        )
        app, user, sid = _ids()
        with pytest.raises(ArtifactTooLargeError, match="exceeds inline threshold"):
            await service.save_artifact(
                app_name=app,
                user_id=user,
                session_id=sid,
                filename="big.bin",
                artifact=_inline_part(b"x" * 129),
            )


class TestVersioning:
    async def test_versions_increment_from_zero(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        versions = []
        for i in range(3):
            v = await service.save_artifact(
                app_name=app,
                user_id=user,
                session_id=sid,
                filename="doc.txt",
                artifact=_text_part(f"revision {i}"),
            )
            versions.append(v)
        assert versions == [0, 1, 2]

    async def test_load_specific_version(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        for i in range(3):
            await service.save_artifact(
                app_name=app,
                user_id=user,
                session_id=sid,
                filename="doc.txt",
                artifact=_text_part(f"revision {i}"),
            )

        # version=None → latest
        latest = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="doc.txt"
        )
        assert latest is not None and latest.text == "revision 2"

        # specific versions
        for v, expected in enumerate(["revision 0", "revision 1", "revision 2"]):
            part = await service.load_artifact(
                app_name=app,
                user_id=user,
                session_id=sid,
                filename="doc.txt",
                version=v,
            )
            assert part is not None and part.text == expected

    async def test_list_versions(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        for i in range(3):
            await service.save_artifact(
                app_name=app,
                user_id=user,
                session_id=sid,
                filename="doc.txt",
                artifact=_text_part(f"v{i}"),
            )

        versions = await service.list_versions(
            app_name=app, user_id=user, session_id=sid, filename="doc.txt"
        )
        assert versions == [0, 1, 2]

    async def test_list_artifact_versions_includes_metadata(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="doc.txt",
            artifact=_text_part("hello"),
            custom_metadata={"source": "crm"},
        )

        versions = await service.list_artifact_versions(
            app_name=app, user_id=user, session_id=sid, filename="doc.txt"
        )
        assert len(versions) == 1
        v = versions[0]
        assert v.version == 0
        assert v.mime_type == "text/plain; charset=utf-8"
        # Custom metadata round-trips alongside the internal part-kind hint.
        assert v.custom_metadata["source"] == "crm"
        assert v.create_time > 0
        # Synthesised URI for inline content.
        assert v.canonical_uri.startswith("kurrentdb+stream://AgentArtifact-")

    async def test_get_artifact_version_latest(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        for i in range(3):
            await service.save_artifact(
                app_name=app,
                user_id=user,
                session_id=sid,
                filename="doc.txt",
                artifact=_text_part(f"v{i}"),
            )
        latest = await service.get_artifact_version(
            app_name=app, user_id=user, session_id=sid, filename="doc.txt"
        )
        assert latest is not None and latest.version == 2


class TestScoping:
    async def test_session_and_user_scopes_are_separate_streams(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="shared.txt",
            artifact=_text_part("session-scoped"),
        )
        await service.save_artifact(
            app_name=app,
            user_id=user,
            filename="shared.txt",
            artifact=_text_part("user-scoped"),
        )

        sp = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="shared.txt"
        )
        up = await service.load_artifact(
            app_name=app, user_id=user, filename="shared.txt"
        )
        assert sp is not None and sp.text == "session-scoped"
        assert up is not None and up.text == "user-scoped"


class TestDelete:
    async def test_delete_makes_load_return_none(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="doc.txt",
            artifact=_text_part("hi"),
        )
        await service.delete_artifact(
            app_name=app, user_id=user, session_id=sid, filename="doc.txt"
        )
        part = await service.load_artifact(
            app_name=app, user_id=user, session_id=sid, filename="doc.txt"
        )
        assert part is None

    async def test_delete_is_idempotent(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        # Delete something that never existed — must not raise.
        await service.delete_artifact(
            app_name=app, user_id=user, session_id=sid, filename="ghost.txt"
        )


class TestListKeys:
    async def test_list_keys_scoped_by_session(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBArtifactService(kurrentdb_client)
        app, user, sid = _ids()
        await service.save_artifact(
            app_name=app,
            user_id=user,
            session_id=sid,
            filename="session_a.txt",
            artifact=_text_part("sa"),
        )
        await service.save_artifact(
            app_name=app,
            user_id=user,
            filename="user_a.txt",
            artifact=_text_part("ua"),
        )

        # Server-side category projection is asynchronous — wait for our keys
        # to appear (or timeout).
        async def get_keys_with_session() -> list[str]:
            return await service.list_artifact_keys(
                app_name=app, user_id=user, session_id=sid
            )

        async def get_user_only_keys() -> list[str]:
            return await service.list_artifact_keys(app_name=app, user_id=user)

        # Category projection is asynchronous; poll until all expected keys
        # land in both scopes or the budget runs out.
        # 240 × 0.25 s = 60 s — enough for a freshly-started Testcontainers
        # container where the $by_category system projection may take longer
        # to process new events than a warmed-up docker-compose instance.
        for _ in range(240):
            session_scope = await get_keys_with_session()
            user_scope = await get_user_only_keys()
            have_all = (
                "session_a.txt" in session_scope
                and "user_a.txt" in session_scope
                and "user_a.txt" in user_scope
            )
            if have_all:
                break
            await asyncio.sleep(0.25)

        assert "session_a.txt" in session_scope
        assert "user_a.txt" in session_scope  # user-scoped always included
        assert user_scope == ["user_a.txt"]  # session-scoped excluded
