"""Integration tests for ``KurrentDBSessionManager``.

Exercise the manager directly (bypassing Strands' hook machinery) against a
live KurrentDB to verify the write + restore round-trip.
"""

from __future__ import annotations

import uuid

from kurrentdbclient import KurrentDBClient

from kurrent_strands import KurrentDBSessionManager


def _ids() -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}", f"session-{suffix}")


class _FakeAgent:
    """Minimal stand-in matching only the surface our SessionManager touches.

    Real ``strands.Agent`` has many more attributes, but ``initialize`` and
    ``append_message`` only read/write ``messages``, ``state``,
    ``conversation_manager``, and ``_interrupt_state`` — so this suffices.
    """

    def __init__(self) -> None:
        self.messages: list = []


class TestAppendAndInitialize:
    def test_first_run_emits_session_started(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        sm.initialize(_FakeAgent())

        # Stream now exists with a SessionStarted event at position 0.
        from kurrent_strands._schema.stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        assert len(records) == 1
        assert records[0].type == "SessionStarted"

    def test_append_and_restore_messages(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        agent_a = _FakeAgent()
        sm.initialize(agent_a)

        messages = [
            {"role": "user", "content": [{"text": "hello"}]},
            {"role": "assistant", "content": [{"text": "hi there"}]},
            {
                "role": "assistant",
                "content": [
                    {"text": "let me check"},
                    {
                        "toolUse": {
                            "toolUseId": "c1",
                            "name": "search",
                            "input": {"q": "kurrent"},
                        }
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "c1",
                            "content": [{"text": "3 hits"}],
                        }
                    }
                ],
            },
        ]
        for msg in messages:
            sm.append_message(msg, agent_a)

        # Fresh manager + fresh agent; restore from the stream.
        sm_b = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        agent_b = _FakeAgent()
        sm_b.initialize(agent_b)
        assert len(agent_b.messages) == 4
        assert agent_b.messages[0]["content"] == [{"text": "hello"}]
        assert agent_b.messages[1]["content"] == [{"text": "hi there"}]
        assert agent_b.messages[2]["content"][0] == {"text": "let me check"}
        tool_use = agent_b.messages[2]["content"][1]["toolUse"]
        assert tool_use["toolUseId"] == "c1"
        assert tool_use["input"] == {"q": "kurrent"}
        tool_result = agent_b.messages[3]["content"][0]["toolResult"]
        assert tool_result["toolUseId"] == "c1"
        assert tool_result["content"] == [{"text": "3 hits"}]

    def test_usage_metadata_attaches_to_assistant_events(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        sm.initialize(_FakeAgent())

        sm.append_message(
            {
                "role": "assistant",
                "content": [{"text": "hi"}],
                "metadata": {
                    "usage": {
                        "inputTokens": 42,
                        "outputTokens": 7,
                        "totalTokens": 49,
                    }
                },
            },
            _FakeAgent(),
        )

        from kurrent_strands._schema.stream_names import for_session
        from kurrent_strands._serialization import read_metadata

        records = kurrentdb_client.get_stream(for_session(sid))
        assistant = next(r for r in records if r.type == "AssistantTextGenerated")
        md = read_metadata(assistant)
        assert md == {
            "$usage": {
                "input_tokens": 42,
                "output_tokens": 7,
                "total_tokens": 49,
            }
        }
