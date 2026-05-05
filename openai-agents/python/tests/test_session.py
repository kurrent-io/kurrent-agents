"""Integration tests for ``KurrentDBSession``.

Exercise the Session directly (bypassing the SDK's Runner) against a live
KurrentDB to verify add_items + get_items + clear_session round-trip.
"""

from __future__ import annotations

import uuid

from kurrentdbclient import AsyncKurrentDBClient

from kurrent_openai_agents import KurrentDBSession


def _ids() -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}", f"session-{suffix}")


class TestAddAndGet:
    async def test_empty_session_returns_empty(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        assert await session.get_items() == []

    async def test_round_trip_conversation(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )

        items = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hi there"}],
            },
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "search",
                "arguments": '{"q": "kurrent"}',
            },
            {
                "type": "function_call_output",
                "call_id": "c1",
                "output": "3 hits",
            },
        ]
        await session.add_items(items)

        # Fresh session object reading the same stream — simulates a
        # new process picking up where the previous one left off.
        session_b = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        restored = await session_b.get_items()
        assert restored == items

    async def test_incremental_appends(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )

        await session.add_items([{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first"}],
        }])
        await session.add_items([{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "response one"}],
        }])
        await session.add_items([{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "second"}],
        }])

        items = await session.get_items()
        assert len(items) == 3
        assert items[0]["content"][0]["text"] == "first"
        assert items[1]["content"][0]["text"] == "response one"
        assert items[2]["content"][0]["text"] == "second"

    async def test_limit_returns_latest_n(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        for i in range(5):
            await session.add_items([{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"msg {i}"}],
            }])

        latest_two = await session.get_items(limit=2)
        assert len(latest_two) == 2
        assert latest_two[0]["content"][0]["text"] == "msg 3"
        assert latest_two[1]["content"][0]["text"] == "msg 4"


class TestClearSession:
    async def test_clear_appends_session_ended(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        await session.add_items([{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hi"}],
        }])
        await session.clear_session()
        # Soft delete — items still readable for audit.
        items = await session.get_items()
        assert len(items) == 1


class TestNonCanonical:
    async def test_reasoning_item_round_trips_verbatim(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        original = {
            "type": "reasoning",
            "content": [{"type": "reasoning_text", "text": "thinking..."}],
            "id": "r1",
        }
        await session.add_items([original])
        [restored] = await session.get_items()
        assert restored == original


class TestThinking:
    async def test_plaintext_reasoning_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        original = {
            "type": "reasoning",
            "id": "r1",
            "content": [{"type": "reasoning_text", "text": "thinking..."}],
        }
        await session.add_items([original])
        [restored] = await session.get_items()
        assert restored == original

    async def test_encrypted_reasoning_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        original = {
            "type": "reasoning",
            "id": "r2",
            "encrypted_content": "AAA-OPAQUE-AAA",
            "signature": "sig-deadbeef",
        }
        await session.add_items([original])
        [restored] = await session.get_items()
        assert restored == original


class TestMcpApprovals:
    async def test_mcp_approval_pair_round_trips(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        session = KurrentDBSession(
            session_id=sid, client=kurrentdb_client, app_name=app, user_id=user
        )
        items = [
            {
                "type": "mcp_approval_request",
                "id": "req-1",
                "name": "publish_post",
                "arguments": '{"title": "hi"}',
                "server_label": "blog-mcp",
            },
            {
                "type": "mcp_approval_response",
                "approval_request_id": "req-1",
                "approve": True,
                "reason": "looks fine",
            },
        ]
        await session.add_items(items)
        restored = await session.get_items()
        assert restored == items
