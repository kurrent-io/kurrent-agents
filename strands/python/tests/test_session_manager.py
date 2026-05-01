"""Integration tests for ``KurrentDBSessionManager``.

Exercise the manager directly (bypassing Strands' hook machinery) against a
live KurrentDB to verify the write + restore round-trip.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from kurrentdbclient import KurrentDBClient

from kurrent_strands import KurrentDBSessionManager
from kurrent_strands.session_manager import _interpret_outcome


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
        from kurrent_strands._stream_names import for_session

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

        from kurrent_strands._serialization import read_metadata
        from kurrent_strands._stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        assistant = next(r for r in records if r.type == "AssistantTextGenerated")
        md = read_metadata(assistant)
        # ``$schema_version`` is stamped by the writer per SCHEMA_v2 §9; the
        # caller-supplied ``$usage`` metadata rides alongside it.
        assert md == {
            "$schema_version": 2,
            "$usage": {
                "input_tokens": 42,
                "output_tokens": 7,
                "total_tokens": 49,
            },
        }


class TestInterruptEmission:
    """Canonical ``InterruptIssued`` / ``InterruptResolved`` events for Strands
    tool approvals (DEV-1661, SCHEMA_v2 §3.3).

    Strands' ``Interrupt`` is post-hoc: ``request_id`` is the model-assigned
    ``toolUseId``, so it equals the eventual
    ``AssistantToolCallsGenerated.tool_calls[].call_id`` and satisfies the
    §3.3 correlation rule trivially.
    """

    def _read_canonical(self, kurrentdb_client: KurrentDBClient, sid: str, type_name: str):
        from kurrent_strands._serialization import deserialize
        from kurrent_strands._stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        return [deserialize(r) for r in records if r.type == type_name]

    def test_emit_interrupt_issued_writes_canonical_event(
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

        sm.emit_interrupt_issued(
            tool_use={
                "toolUseId": "tool_use_xyz",
                "name": "send_email",
                "input": {"to": "alice@example.com", "subject": "hi"},
            },
        )

        [issued] = self._read_canonical(kurrentdb_client, sid, "InterruptIssued")
        assert issued.request_id == "tool_use_xyz"
        assert issued.kind == "approval"
        assert issued.tool_name == "send_email"
        # message_id is null per DEV-1661 (Strands fires before appending the
        # carrier message; standalone interrupt per SCHEMA_v2 §3.3).
        assert not issued.HasField("message_id")

        from google.protobuf.json_format import MessageToDict

        from kurrent_strands._codec import STRANDS_EXTENSION_KEY

        ext = MessageToDict(
            issued.extensions[STRANDS_EXTENSION_KEY],
            preserving_proto_field_name=True,
        )
        # Soft convention from §3.3 — proposed_call lets cross-framework
        # readers (Capacitor) render the approval prompt without per-slug code.
        assert ext["interrupt"]["proposed_call"] == {
            "id": "tool_use_xyz",
            "name": "send_email",
            "arguments": {"to": "alice@example.com", "subject": "hi"},
        }

    def test_emit_interrupt_resolved_allow_round_trips_outcome(
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

        sm.emit_interrupt_resolved(
            tool_use_id="tool_use_xyz", outcome="allow"
        )

        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert resolved.request_id == "tool_use_xyz"
        assert resolved.outcome == "allow"

    def test_emit_interrupt_resolved_with_user_response(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """Free-form user response rides under
        ``extensions.strands.interrupt.resolution`` for same-framework
        replay; the canonical ``outcome`` field is the cross-SDK summary."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_resolved(
            tool_use_id="tool_use_xyz",
            outcome="answered",
            response={"reason": "user clarified intent", "extra": 42},
        )

        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert resolved.outcome == "answered"

        from google.protobuf.json_format import MessageToDict

        from kurrent_strands._codec import STRANDS_EXTENSION_KEY

        ext = MessageToDict(
            resolved.extensions[STRANDS_EXTENSION_KEY],
            preserving_proto_field_name=True,
        )
        assert ext["interrupt"]["resolution"] == {
            "reason": "user clarified intent",
            "extra": 42,
        }

    def test_issued_then_deny_pair_round_trips(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """End-to-end: an interrupt issued and then denied yields two
        canonical events on the stream with matching ``request_id``."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_issued(
            tool_use={
                "toolUseId": "call_42",
                "name": "delete_record",
                "input": {"id": "abc"},
            },
        )
        sm.emit_interrupt_resolved(tool_use_id="call_42", outcome="deny")

        [issued] = self._read_canonical(kurrentdb_client, sid, "InterruptIssued")
        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert issued.request_id == resolved.request_id == "call_42"
        assert issued.kind == "approval"
        assert resolved.outcome == "deny"

    def test_unresolved_interrupt_is_present_without_resolution(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """An interrupt with no resolution by session end appears as a sole
        ``InterruptIssued`` event — no ``InterruptResolved`` is fabricated."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client,
            session_id=sid,
            app_name=app,
            user_id=user,
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_issued(
            tool_use={
                "toolUseId": "abandoned",
                "name": "search",
                "input": {},
            },
        )

        issued_events = self._read_canonical(
            kurrentdb_client, sid, "InterruptIssued"
        )
        resolved_events = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert len(issued_events) == 1
        assert resolved_events == []


def _interrupt(tool_use_id: str, *, response: Any = None) -> Any:
    """Build a real ``strands.interrupt.Interrupt`` whose id embeds the given
    ``tool_use_id`` (matching the framework's id format)."""
    from strands.interrupt import Interrupt

    return Interrupt(
        id=f"v1:before_tool_call:{tool_use_id}:{uuid.uuid4()}",
        name="approval",
        reason={"tool_use_id": tool_use_id},
        response=response,
    )


def _fake_before_tool_call_event(tool_use_id: str, *, interrupts: list[Any]) -> Any:
    """Stand-in for ``strands.hooks.BeforeToolCallEvent`` carrying just the
    fields :meth:`KurrentDBSessionManager._on_before_tool_call` reads."""
    state = SimpleNamespace(interrupts={i.id: i for i in interrupts})
    agent = SimpleNamespace(_interrupt_state=state)
    tool_use = {"toolUseId": tool_use_id, "name": "send_email", "input": {"to": "alice"}}
    return SimpleNamespace(agent=agent, tool_use=tool_use)


class TestInterruptObserver:
    """The observer hook (``_on_before_tool_call``) auto-emits canonical
    interrupt events by inspecting ``agent._interrupt_state``. Pairs with
    the explicit ``emit_interrupt_*`` API used by user hooks that prefer
    direct control."""

    def _read_canonical(self, kurrentdb_client: KurrentDBClient, sid: str, type_name: str):
        from kurrent_strands._serialization import deserialize
        from kurrent_strands._stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        return [deserialize(r) for r in records if r.type == type_name]

    def test_observer_emits_issued_when_interrupt_state_populated(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        event = _fake_before_tool_call_event(
            "tool_use_xyz", interrupts=[_interrupt("tool_use_xyz")]
        )
        sm._on_before_tool_call(event)

        [issued] = self._read_canonical(kurrentdb_client, sid, "InterruptIssued")
        assert issued.request_id == "tool_use_xyz"
        # No resolution yet — response was None on the Interrupt.
        assert self._read_canonical(kurrentdb_client, sid, "InterruptResolved") == []

    def test_observer_does_not_reemit_already_issued(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        event = _fake_before_tool_call_event(
            "tool_use_xyz", interrupts=[_interrupt("tool_use_xyz")]
        )
        sm._on_before_tool_call(event)
        sm._on_before_tool_call(event)  # second fire — must be a no-op

        issued = self._read_canonical(kurrentdb_client, sid, "InterruptIssued")
        assert len(issued) == 1

    def test_observer_emits_resolved_when_response_populated(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        # First dispatch — interrupt raised, response not yet provided.
        sm._on_before_tool_call(
            _fake_before_tool_call_event(
                "tool_use_xyz", interrupts=[_interrupt("tool_use_xyz")]
            )
        )
        # Second dispatch (resumed) — same interrupt, response populated.
        sm._on_before_tool_call(
            _fake_before_tool_call_event(
                "tool_use_xyz",
                interrupts=[_interrupt("tool_use_xyz", response="allow")],
            )
        )

        [issued] = self._read_canonical(kurrentdb_client, sid, "InterruptIssued")
        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert issued.request_id == resolved.request_id == "tool_use_xyz"
        assert resolved.outcome == "allow"

    def test_observer_no_op_when_interrupt_state_empty(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        # Empty interrupts dict — common path when no user hook raised.
        sm._on_before_tool_call(
            _fake_before_tool_call_event("tool_use_xyz", interrupts=[])
        )
        assert self._read_canonical(kurrentdb_client, sid, "InterruptIssued") == []

    def test_initialize_pre_populates_dedupe_from_stream(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """A resumed session must not re-emit interrupts that already live
        in the stream — `initialize` reconstructs the dedupe sets."""
        app, user, sid = _ids()
        # Session 1: emit Issued via the explicit API.
        sm1 = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm1.initialize(_FakeAgent())
        sm1.emit_interrupt_issued(
            tool_use={"toolUseId": "tool_use_xyz", "name": "send_email", "input": {}}
        )

        # Session 2: resume — initialize() must populate the issued tracker.
        sm2 = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm2.initialize(_FakeAgent())
        # Observer fires with the same interrupt still active — must not duplicate.
        sm2._on_before_tool_call(
            _fake_before_tool_call_event(
                "tool_use_xyz", interrupts=[_interrupt("tool_use_xyz")]
            )
        )
        issued = self._read_canonical(kurrentdb_client, sid, "InterruptIssued")
        assert len(issued) == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (None, "timeout"),
        (True, "allow"),
        (False, "deny"),
        ("allow", "allow"),
        ("ALLOW", "allow"),
        # The ``allow_once`` / ``allow_always`` shades survive round-trip;
        # they are NOT collapsed to plain ``allow`` (SCHEMA_v2 §3.3).
        ("allow_once", "allow_once"),
        ("allow_always", "allow_always"),
        ("answered", "answered"),
        ("timeout", "timeout"),
        ("approve", "allow"),
        ("yes", "allow"),
        ("deny", "deny"),
        ("rejected", "deny"),
        ("no", "deny"),
        ("cancel", "cancel"),
        ({"approve": True}, "allow"),
        ({"approve": False}, "deny"),
        ({"decision": "allow"}, "allow"),
        # Case-insensitive + whitespace-tolerant: ``decision`` is normalised
        # via strip+lower before matching, so common stylings round-trip.
        ({"decision": "ALLOW"}, "allow"),
        ({"decision": " deny "}, "deny"),
        ({"decision": "Cancel"}, "cancel"),
        ({"decision": "allow_once"}, "allow_once"),
        ({"decision": "allow_always"}, "allow_always"),
        ({"decision": "deny"}, "deny"),
        ({"decision": "cancel"}, "cancel"),
        ({"freeform": "user typed something"}, "answered"),
        ("anything else", "answered"),
    ],
)
def test_interpret_outcome_maps_common_responses(response: Any, expected: str) -> None:
    assert _interpret_outcome(response) == expected


class TestPresenceAwareEmission:
    """Proto Edition 2024 makes explicit field presence the default — an
    explicitly-set empty string differs from an unset field via
    ``HasField``. The emitters must not stamp empty/missing values onto
    optional canonical fields."""

    def _read_canonical(self, kurrentdb_client: KurrentDBClient, sid: str, type_name: str):
        from kurrent_strands._serialization import deserialize
        from kurrent_strands._stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        return [deserialize(r) for r in records if r.type == type_name]

    def test_issued_without_tool_name_leaves_field_unset(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_issued(
            tool_use={"toolUseId": "tu", "name": "", "input": {}}
        )

        [issued] = self._read_canonical(
            kurrentdb_client, sid, "InterruptIssued"
        )
        assert not issued.HasField("tool_name")

        from google.protobuf.json_format import MessageToDict

        from kurrent_strands._codec import STRANDS_EXTENSION_KEY

        ext = MessageToDict(
            issued.extensions[STRANDS_EXTENSION_KEY],
            preserving_proto_field_name=True,
        )
        # ``proposed_call.name`` follows suit — absent rather than empty.
        assert "name" not in ext["interrupt"]["proposed_call"]

    def test_resolved_with_string_response_sets_canonical_field(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """SCHEMA_v2 §3.3 ``InterruptResolved.response`` is the canonical
        home for free-form text rationale; cross-SDK readers find it
        without decoding the strands extension."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_resolved(
            tool_use_id="tu",
            outcome="answered",
            response="  user gave a clarification  ",
        )

        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert resolved.HasField("response")
        # Whitespace is stripped — the canonical field carries the trimmed text.
        assert resolved.response == "user gave a clarification"

    def test_resolved_with_dict_response_leaves_canonical_field_unset(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """A structured (dict / list) response stays only in
        ``extensions.strands.interrupt.resolution``; coercing it via
        ``str()`` would surface noise like ``"{'approve': True}"`` which
        is not human-readable rationale."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_resolved(
            tool_use_id="tu",
            outcome="allow",
            response={"approve": True, "by": "ops"},
        )

        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert not resolved.HasField("response")

        from google.protobuf.json_format import MessageToDict

        from kurrent_strands._codec import STRANDS_EXTENSION_KEY

        ext = MessageToDict(
            resolved.extensions[STRANDS_EXTENSION_KEY],
            preserving_proto_field_name=True,
        )
        assert ext["interrupt"]["resolution"] == {"approve": True, "by": "ops"}

    def test_resolved_with_empty_string_leaves_canonical_field_unset(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """Empty / whitespace-only strings don't add information; treat
        them the same as no response for the canonical field."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        sm.emit_interrupt_resolved(
            tool_use_id="tu", outcome="answered", response="   "
        )

        [resolved] = self._read_canonical(
            kurrentdb_client, sid, "InterruptResolved"
        )
        assert not resolved.HasField("response")


class TestEmitValidation:
    """Boundary validation on the explicit emission API."""

    def test_emit_interrupt_issued_rejects_empty_tool_use_id(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())
        with pytest.raises(ValueError, match="toolUseId"):
            sm.emit_interrupt_issued(
                tool_use={"toolUseId": "", "name": "x", "input": {}}
            )

    def test_emit_interrupt_resolved_rejects_non_canonical_outcome(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())
        with pytest.raises(ValueError, match="canonical"):
            sm.emit_interrupt_resolved(tool_use_id="c1", outcome="approved")

    def test_emit_interrupt_issued_handles_json_string_input(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """Some Strands adapters pass ``tool_use['input']`` as a JSON string;
        the codec already coerces, the emit path now does the same."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())
        sm.emit_interrupt_issued(
            tool_use={
                "toolUseId": "tu",
                "name": "lookup",
                "input": '{"q": "kurrent"}',
            }
        )

        from google.protobuf.json_format import MessageToDict

        from kurrent_strands._codec import STRANDS_EXTENSION_KEY
        from kurrent_strands._serialization import deserialize
        from kurrent_strands._stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        issued = next(
            deserialize(r) for r in records if r.type == "InterruptIssued"
        )
        ext = MessageToDict(
            issued.extensions[STRANDS_EXTENSION_KEY],
            preserving_proto_field_name=True,
        )
        assert ext["interrupt"]["proposed_call"]["arguments"] == {"q": "kurrent"}


class TestObserverDedupeWithExplicit:
    """The observer + explicit emission paths share dedupe state, so a user
    hook that calls ``emit_interrupt_issued`` directly never produces a
    duplicate event when the observer runs in the same dispatch."""

    def _read_canonical(self, kurrentdb_client: KurrentDBClient, sid: str, type_name: str):
        from kurrent_strands._serialization import deserialize
        from kurrent_strands._stream_names import for_session

        records = kurrentdb_client.get_stream(for_session(sid))
        return [deserialize(r) for r in records if r.type == type_name]

    def test_observer_does_not_re_emit_after_explicit_emit(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        # User hook calls explicit API ahead of the observer.
        sm.emit_interrupt_issued(
            tool_use={"toolUseId": "tu", "name": "x", "input": {}}
        )
        # Observer fires next on the same tool call — must not duplicate.
        sm._on_before_tool_call(
            _fake_before_tool_call_event("tu", interrupts=[_interrupt("tu")])
        )
        assert (
            len(self._read_canonical(kurrentdb_client, sid, "InterruptIssued"))
            == 1
        )

    def test_observer_does_not_match_substring_tool_use_ids(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        """A short ``toolUseId`` like ``c1`` must not be mis-matched against
        an interrupt whose id contains ``c12`` etc. The observer matches by
        exact prefix on the framework's id format."""
        app, user, sid = _ids()
        sm = KurrentDBSessionManager(
            client=kurrentdb_client, session_id=sid, app_name=app, user_id=user
        )
        sm.initialize(_FakeAgent())

        # _interrupt_state contains an interrupt for ``c12345`` (not ``c1``).
        sm._on_before_tool_call(
            _fake_before_tool_call_event("c1", interrupts=[_interrupt("c12345")])
        )
        assert (
            self._read_canonical(kurrentdb_client, sid, "InterruptIssued") == []
        )
