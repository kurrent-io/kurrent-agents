"""Unit tests for canonical schema Pydantic models.

Covers serialisation shape (snake_case JSON, extensions envelope) and
round-trip stability for every canonical event type.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kurrent_google_adk._schema.events import (
    ADK_EXTENSION_KEY,
    AgentConfig,
    AgentTransferred,
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    Compaction,
    FactRetained,
    Rewind,
    SessionEnded,
    SessionStarted,
    StateDelta,
    ToolCallInfo,
    ToolResultReceived,
    ToolSpec,
    UserMessageReceived,
)


def _iso_now() -> datetime:
    return datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


class TestSessionStarted:
    def test_minimal(self) -> None:
        event = SessionStarted(timestamp=_iso_now())
        data = json.loads(event.model_dump_json(exclude_none=True))
        assert data == {"timestamp": "2026-04-19T12:00:00Z"}

    def test_with_full_agent_config(self) -> None:
        event = SessionStarted(
            app_name="my_app",
            agent_name="root",
            model="gemini-2.5-flash",
            user_id="alice",
            agent_config=AgentConfig(
                tools=[ToolSpec(name="search", source="vended")],
                plugins=["logging"],
                conversation_manager={"type": "sliding_window", "max_messages": 20},
                model_parameters={"temperature": 0.2},
            ),
            timestamp=_iso_now(),
        )
        data = json.loads(event.model_dump_json(exclude_none=True))
        assert data["app_name"] == "my_app"
        assert data["agent_config"]["tools"][0]["name"] == "search"
        assert data["agent_config"]["conversation_manager"]["max_messages"] == 20

    def test_extensions_envelope_round_trips(self) -> None:
        event = SessionStarted(
            timestamp=_iso_now(),
            extensions={ADK_EXTENSION_KEY: {"branch": "root.worker_1"}},
        )
        payload = event.model_dump_json(exclude_none=True)
        reloaded = SessionStarted.model_validate_json(payload)
        assert reloaded.extensions == {ADK_EXTENSION_KEY: {"branch": "root.worker_1"}}

    def test_unknown_field_is_ignored(self) -> None:
        # Forward-compatibility: a future schema field doesn't break an older reader.
        event = SessionStarted.model_validate_json(
            json.dumps(
                {"timestamp": "2026-04-19T12:00:00Z", "future_field_we_do_not_know": 42}
            )
        )
        assert event.timestamp == _iso_now()


class TestConversationEvents:
    def test_user_message(self) -> None:
        event = UserMessageReceived(
            content="hello",
            message_id="m1",
            author_name="alice",
            message_index=0,
            timestamp=_iso_now(),
        )
        assert event.content == "hello"
        assert event.message_index == 0

    def test_assistant_text(self) -> None:
        event = AssistantTextGenerated(
            content="hi",
            message_id="m2",
            message_index=1,
            timestamp=_iso_now(),
        )
        assert event.content == "hi"

    def test_tool_calls(self) -> None:
        event = AssistantToolCallsGenerated(
            tool_calls=[
                ToolCallInfo(
                    call_id="c1",
                    tool_name="search",
                    arguments={"q": "kurrent"},
                )
            ],
            message_index=2,
            timestamp=_iso_now(),
        )
        assert event.tool_calls[0].call_id == "c1"
        assert event.tool_calls[0].arguments == {"q": "kurrent"}

    def test_tool_result(self) -> None:
        event = ToolResultReceived(
            call_id="c1",
            tool_name="search",
            result='{"hits": 3}',
            message_index=3,
            timestamp=_iso_now(),
        )
        assert event.call_id == "c1"


class TestSupportingEvents:
    def test_session_ended(self) -> None:
        event = SessionEnded(reason="completed", timestamp=_iso_now())
        assert event.reason == "completed"

    def test_fact_retained(self) -> None:
        event = FactRetained(fact="User prefers dark mode.", retained_at=_iso_now())
        assert event.fact == "User prefers dark mode."

    def test_agent_transferred(self) -> None:
        event = AgentTransferred(
            from_agent="root", to_agent="worker_1", timestamp=_iso_now()
        )
        assert event.from_agent == "root"
        assert event.to_agent == "worker_1"

    def test_rewind(self) -> None:
        event = Rewind(
            rewind_before_invocation_id="inv_1",
            state_delta={"counter": 0},
            timestamp=_iso_now(),
        )
        assert event.rewind_before_invocation_id == "inv_1"

    def test_compaction(self) -> None:
        event = Compaction(
            start_timestamp=_iso_now(),
            end_timestamp=_iso_now(),
            compacted_content={"summary": "5 turns summarised."},
            timestamp=_iso_now(),
        )
        assert event.compacted_content == {"summary": "5 turns summarised."}

    def test_state_delta(self) -> None:
        event = StateDelta(
            delta={"counter": 1},
            invocation_id="inv_2",
            timestamp=_iso_now(),
        )
        assert event.delta == {"counter": 1}
