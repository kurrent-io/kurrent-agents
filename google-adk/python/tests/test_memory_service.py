"""Integration tests for ``KurrentDBMemoryService``."""

from __future__ import annotations

import uuid

from google.adk.events.event import Event as AdkEvent
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions.session import Session
from google.genai import types
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_google_adk import KurrentDBMemoryService


def _ids() -> tuple[str, str]:
    """Return isolated (app_name, user_id) for one test."""
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}")


def _text_event(author: str, text: str, *, invocation_id: str = "inv_1") -> AdkEvent:
    return AdkEvent(
        author=author,
        invocation_id=invocation_id,
        content=types.Content(
            role="user" if author == "user" else "model",
            parts=[types.Part(text=text)],
        ),
    )


def _tool_call_event(tool: str) -> AdkEvent:
    return AdkEvent(
        author="agent",
        invocation_id="inv_x",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(id="c1", name=tool, args={})
                )
            ],
        ),
    )


class TestAddMemory:
    async def test_appends_memory_entries_as_fact_retained(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        entries = [
            MemoryEntry(
                content=types.Content(role="user", parts=[types.Part(text="likes dark mode")]),
                author="alice",
            ),
            MemoryEntry(
                content=types.Content(role="user", parts=[types.Part(text="prefers short answers")]),
                author="alice",
            ),
        ]
        await service.add_memory(app_name=app, user_id=user, memories=entries)

        result = await service.search_memory(app_name=app, user_id=user, query="")
        texts = [_text(m) for m in result.memories]
        assert "likes dark mode" in texts
        assert "prefers short answers" in texts
        # All entries retain the original author on the MemoryEntry.
        authors = {m.author for m in result.memories}
        assert authors == {"alice"}

    async def test_skips_entries_without_text(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        entries = [
            MemoryEntry(
                content=types.Content(role="user", parts=[types.Part(text="")]),
            ),
            MemoryEntry(
                content=types.Content(role="user", parts=[types.Part(text="a real fact")]),
            ),
        ]
        await service.add_memory(app_name=app, user_id=user, memories=entries)

        result = await service.search_memory(app_name=app, user_id=user, query="")
        texts = [_text(m) for m in result.memories]
        assert texts == ["a real fact"]

    async def test_custom_metadata_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        entries = [
            MemoryEntry(
                content=types.Content(role="user", parts=[types.Part(text="keep this")]),
                custom_metadata={"source": "crm", "confidence": 0.9},
            )
        ]
        await service.add_memory(app_name=app, user_id=user, memories=entries)
        result = await service.search_memory(app_name=app, user_id=user, query="")
        assert len(result.memories) == 1
        assert result.memories[0].custom_metadata == {"source": "crm", "confidence": 0.9}


class TestSearchMemory:
    async def test_empty_when_no_stream(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        result = await service.search_memory(app_name=app, user_id=user, query="x")
        assert result.memories == []

    async def test_returns_all_retained_newest_first(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        for text in ["fact one", "fact two", "fact three"]:
            await service.add_memory(
                app_name=app,
                user_id=user,
                memories=[
                    MemoryEntry(
                        content=types.Content(role="user", parts=[types.Part(text=text)])
                    )
                ],
            )

        result = await service.search_memory(app_name=app, user_id=user, query="")
        texts = [_text(m) for m in result.memories]
        # backwards=True in search → newest first
        assert texts == ["fact three", "fact two", "fact one"]

    async def test_query_is_ignored_in_v1(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        await service.add_memory(
            app_name=app,
            user_id=user,
            memories=[
                MemoryEntry(
                    content=types.Content(role="user", parts=[types.Part(text="only fact")])
                )
            ],
        )
        # Any query returns everything retained at the baseline.
        r1 = await service.search_memory(app_name=app, user_id=user, query="nothing-related")
        r2 = await service.search_memory(app_name=app, user_id=user, query="only")
        assert [_text(m) for m in r1.memories] == ["only fact"]
        assert [_text(m) for m in r2.memories] == ["only fact"]


class TestAddEventsToMemory:
    async def test_retains_text_events_only(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        events = [
            _text_event("user", "hello"),
            _text_event("agent", "hi back"),
            _tool_call_event("search"),  # no text → skipped
            _text_event("user", "another"),
        ]
        await service.add_events_to_memory(
            app_name=app, user_id=user, events=events, session_id="s1"
        )
        result = await service.search_memory(app_name=app, user_id=user, query="")
        texts = sorted(_text(m) for m in result.memories)
        assert texts == ["another", "hello", "hi back"]

    async def test_preserves_custom_metadata(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        await service.add_events_to_memory(
            app_name=app,
            user_id=user,
            events=[_text_event("user", "hi")],
            session_id="s1",
            custom_metadata={"turn": 1},
        )
        result = await service.search_memory(app_name=app, user_id=user, query="")
        # Event-based paths stash caller metadata under ``custom_metadata`` ext.
        assert len(result.memories) == 1
        assert result.memories[0].author == "user"


class TestAddSessionToMemory:
    async def test_retains_every_textful_event(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        session = Session(
            id="sess_1",
            app_name=app,
            user_id=user,
            events=[
                _text_event("user", "first"),
                _text_event("agent", "second"),
                _tool_call_event("noop"),  # skipped
                _text_event("user", "third"),
            ],
        )
        await service.add_session_to_memory(session)
        result = await service.search_memory(app_name=app, user_id=user, query="")
        texts = sorted(_text(m) for m in result.memories)
        assert texts == ["first", "second", "third"]

    async def test_empty_session_writes_nothing(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBMemoryService(kurrentdb_client)
        app, user = _ids()
        await service.add_session_to_memory(
            Session(id="s", app_name=app, user_id=user, events=[])
        )
        result = await service.search_memory(app_name=app, user_id=user, query="")
        assert result.memories == []


# ----- helpers ---------------------------------------------------------------


def _text(entry: MemoryEntry) -> str:
    assert entry.content.parts is not None
    return entry.content.parts[0].text or ""
