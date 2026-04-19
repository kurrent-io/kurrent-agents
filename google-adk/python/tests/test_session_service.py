"""Integration tests for ``KurrentDBSessionService``.

Require a running KurrentDB at the default endpoint (or
``KURRENTDB_CONNECTION_STRING`` env var). Auto-skipped when unreachable.
"""

from __future__ import annotations

import uuid

import pytest
from google.adk.events.event import Event as AdkEvent
from google.adk.events.event_actions import EventActions
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_google_adk import KurrentDBSessionService, StaleSessionError


def _new_session_ids() -> tuple[str, str, str]:
    """Return isolated (app, user, session) ids for one test."""
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}", f"session_{suffix}")


def _user_text_event(text: str, *, invocation_id: str = "inv_1") -> AdkEvent:
    return AdkEvent(
        author="user",
        invocation_id=invocation_id,
        content=types.Content(role="user", parts=[types.Part(text=text)]),
    )


def _assistant_text_event(text: str, *, invocation_id: str = "inv_1") -> AdkEvent:
    return AdkEvent(
        author="root_agent",
        invocation_id=invocation_id,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


class TestCreateSession:
    async def test_returns_session_with_provided_id(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(
            app_name=app, user_id=user, session_id=sid, state={"seed": 1}
        )
        assert session.id == sid
        assert session.app_name == app
        assert session.user_id == user
        assert session.state == {"seed": 1}
        assert session.events == []

    async def test_generates_session_id_when_absent(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, _ = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user)
        assert session.id
        assert len(session.id) >= 8


class TestAppendAndGet:
    async def test_round_trip_single_turn(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)

        await service.append_event(session, _user_text_event("hello"))
        await service.append_event(session, _assistant_text_event("hi back"))

        reloaded = await service.get_session(app_name=app, user_id=user, session_id=sid)
        assert reloaded is not None
        assert len(reloaded.events) == 2
        assert reloaded.events[0].author == "user"
        assert reloaded.events[0].content.parts[0].text == "hello"
        assert reloaded.events[1].author == "root_agent"
        assert reloaded.events[1].content.parts[0].text == "hi back"

    async def test_missing_session_returns_none(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        result = await service.get_session(
            app_name="app", user_id="user", session_id="does_not_exist_" + uuid.uuid4().hex
        )
        assert result is None

    async def test_tool_call_and_result_round_trip(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)

        await service.append_event(session, _user_text_event("search for x"))
        await service.append_event(
            session,
            AdkEvent(
                author="root_agent",
                invocation_id="inv_1",
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="c1", name="search", args={"q": "x"}
                            )
                        )
                    ],
                ),
            ),
        )
        await service.append_event(
            session,
            AdkEvent(
                author="user",
                invocation_id="inv_1",
                content=types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id="c1", name="search", response={"hits": 3}
                            )
                        )
                    ],
                ),
            ),
        )

        reloaded = await service.get_session(app_name=app, user_id=user, session_id=sid)
        assert reloaded is not None
        assert len(reloaded.events) == 3
        assert reloaded.events[1].get_function_calls()[0].name == "search"
        assert reloaded.events[2].get_function_responses()[0].response == {"hits": 3}


class TestState:
    async def test_state_delta_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)

        await service.append_event(
            session,
            AdkEvent(
                author="root_agent",
                invocation_id="inv_1",
                content=types.Content(role="model", parts=[types.Part(text="ok")]),
                actions=EventActions(state_delta={"counter": 1, "last_query": "x"}),
            ),
        )

        reloaded = await service.get_session(app_name=app, user_id=user, session_id=sid)
        assert reloaded is not None
        assert reloaded.state == {"counter": 1, "last_query": "x"}


class TestGetSessionConfig:
    async def test_num_recent_events_filter(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)

        for i in range(5):
            await service.append_event(session, _user_text_event(f"msg {i}"))

        # Last two events only
        reloaded = await service.get_session(
            app_name=app,
            user_id=user,
            session_id=sid,
            config=GetSessionConfig(num_recent_events=2),
        )
        assert reloaded is not None
        assert len(reloaded.events) == 2
        assert reloaded.events[0].content.parts[0].text == "msg 3"
        assert reloaded.events[1].content.parts[0].text == "msg 4"

    async def test_num_recent_events_zero_returns_no_events(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)
        await service.append_event(session, _user_text_event("ignored"))

        reloaded = await service.get_session(
            app_name=app,
            user_id=user,
            session_id=sid,
            config=GetSessionConfig(num_recent_events=0),
        )
        assert reloaded is not None
        assert reloaded.events == []


class TestUsageMetadata:
    async def test_usage_metadata_round_trips_on_reload(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Regression test for DEV-1479.

        Assistant events written with ``$usage`` metadata must emerge with
        ``Event.usage_metadata`` populated on ``get_session`` — even though
        the v1 codec doesn't round-trip LlmResponse metadata in-band.
        """
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)

        assistant_event = AdkEvent(
            author="root_agent",
            invocation_id="inv_1",
            content=types.Content(role="model", parts=[types.Part(text="hello")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100,
                candidates_token_count=25,
                total_token_count=125,
                cached_content_token_count=0,
            ),
        )
        await service.append_event(session, assistant_event)

        reloaded = await service.get_session(
            app_name=app, user_id=user, session_id=sid
        )
        assert reloaded is not None and len(reloaded.events) == 1
        usage = reloaded.events[0].usage_metadata
        assert usage is not None
        assert usage.prompt_token_count == 100
        assert usage.candidates_token_count == 25
        assert usage.total_token_count == 125
        assert usage.cached_content_token_count == 0


class TestDelete:
    async def test_soft_delete_appends_session_ended(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        service = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()
        session = await service.create_session(app_name=app, user_id=user, session_id=sid)
        await service.append_event(session, _user_text_event("hi"))

        await service.delete_session(app_name=app, user_id=user, session_id=sid)

        # Session is still readable — soft delete preserves history.
        reloaded = await service.get_session(app_name=app, user_id=user, session_id=sid)
        assert reloaded is not None
        assert len(reloaded.events) == 1  # the user msg; SessionEnded is lifecycle-only


class TestConcurrency:
    async def test_two_services_racing_append_raises_stale_session(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Second writer re-reads then retries; a third concurrent write raises."""
        service_a = KurrentDBSessionService(kurrentdb_client)
        service_b = KurrentDBSessionService(kurrentdb_client)
        app, user, sid = _new_session_ids()

        # Service A creates the session; B learns of it by reading.
        session_a = await service_a.create_session(app_name=app, user_id=user, session_id=sid)
        session_b = await service_b.get_session(app_name=app, user_id=user, session_id=sid)
        assert session_b is not None

        # Both append at revision 0 (SessionStarted). A wins, B retries and wins too.
        await service_a.append_event(session_a, _user_text_event("from A"))
        await service_b.append_event(session_b, _user_text_event("from B"))

        # Now manually advance A by another write before B tries again.
        await service_a.append_event(session_a, _user_text_event("A again"))
        # B's in-memory revision is now stale by two events.
        # First B write succeeds after catch-up + retry.
        await service_b.append_event(session_b, _user_text_event("B again"))

        reloaded = await service_a.get_session(
            app_name=app, user_id=user, session_id=sid
        )
        assert reloaded is not None
        # All four appends should be visible.
        assert len(reloaded.events) == 4
