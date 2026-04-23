"""Integration tests for ``KurrentDBSessionStore``.

Cover the required methods (``append``, ``load``) against a live KurrentDB.
These tests bypass the Claude Agent SDK's subprocess machinery entirely —
we drive the adapter directly with synthetic JSONL-shaped dicts to verify
the round-trip invariant.
"""

from __future__ import annotations

import uuid

import pytest
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_claude_agent_sdk import KurrentDBSessionStore
from kurrent_claude_agent_sdk.session_store import _subagent_id_from_subpath


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return (f"project_{suffix}", f"session-{suffix}")


def _entry(**fields) -> dict:
    """Synthetic JSONL entry mimicking the CLI transcript shape."""
    base = {
        "type": "user",
        "uuid": uuid.uuid4().hex,
        "timestamp": "2026-04-19T12:00:00Z",
    }
    base.update(fields)
    return base


class TestAppendAndLoad:
    async def test_load_missing_returns_none(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        key = {"project_key": project, "session_id": sid}
        assert await store.load(key) is None

    async def test_round_trip_preserves_entries_verbatim(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """``load(append(entries)) == entries`` — the one invariant the SDK requires."""
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        key = {"project_key": project, "session_id": sid}

        entries = [
            _entry(
                type="user",
                message={"content": "hello", "role": "user"},
                extra_field={"nested": [1, 2, 3]},
            ),
            _entry(
                type="assistant",
                message={
                    "content": [{"type": "text", "text": "hi there"}],
                    "role": "assistant",
                },
                usage={"input_tokens": 10, "output_tokens": 4},
            ),
            _entry(
                type="system",
                subtype="init",
                data={"tools": ["bash", "edit"]},
            ),
        ]
        await store.append(key, entries)
        restored = await store.load(key)
        assert restored == entries

    async def test_append_is_incremental(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        key = {"project_key": project, "session_id": sid}

        await store.append(key, [_entry(uuid="e1")])
        await store.append(key, [_entry(uuid="e2")])
        await store.append(key, [_entry(uuid="e3")])
        restored = await store.load(key)
        assert [e["uuid"] for e in restored] == ["e1", "e2", "e3"]

    async def test_append_exception_is_swallowed(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Per SDK contract: exceptions are logged, subprocess keeps running."""
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        # Inject an impossible entry (tuple instead of dict) — serialization
        # should explode internally but append must still return normally.
        key = {"project_key": project, "session_id": sid}
        await store.append(key, [("not", "a", "dict")])  # type: ignore[list-item]
        # And a subsequent real append still works.
        await store.append(key, [_entry(uuid="e_ok")])
        restored = await store.load(key)
        assert restored is not None and [e["uuid"] for e in restored] == ["e_ok"]


class TestSubpathScoping:
    async def test_main_and_subagent_transcripts_are_separate(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        main_key = {"project_key": project, "session_id": sid}
        sub_key = {
            "project_key": project,
            "session_id": sid,
            "subpath": "subagents/agent-abc123",
        }

        await store.append(main_key, [_entry(uuid="main-1")])
        await store.append(sub_key, [_entry(uuid="sub-1"), _entry(uuid="sub-2")])

        main_entries = await store.load(main_key)
        sub_entries = await store.load(sub_key)

        assert main_entries is not None
        assert [e["uuid"] for e in main_entries] == ["main-1"]
        assert sub_entries is not None
        assert [e["uuid"] for e in sub_entries] == ["sub-1", "sub-2"]

    async def test_subagent_stream_uses_v2_naming(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Schema v2 §3.5: ``AgentSubsession-{session_id}-{agent_id}``
        replaces the v1 ``AgentSession-{id}__{subpath}`` name-mangling.
        ``agent_id`` is the final path component of ``SessionKey.subpath``.
        """
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        sub_key = {
            "project_key": project,
            "session_id": sid,
            "subpath": "subagents/agent-xyz789",
        }
        await store.append(sub_key, [_entry(uuid="sub-1")])

        expected_stream = f"AgentSubsession-{sid}-agent-xyz789"
        recorded = await kurrentdb_client.get_stream(expected_stream)
        types = [r.type for r in recorded]
        # v2 §3.5: subagent streams do not carry their own ``SessionStarted``.
        assert "SessionStarted" not in types
        assert types.count("ClaudeSDKEntry") == 1

    async def test_main_stream_writes_session_started_once(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Main transcript keeps its ``SessionStarted`` marker; idempotent
        across successive appends within the same process. The first append
        bundles ``SessionStarted`` + entries into a single ``StreamState.ANY``
        write (SCHEMA_v2 §3.1 + repo convention for non-ADK integrations)."""
        store = KurrentDBSessionStore(kurrentdb_client)
        project, sid = _ids()
        key = {"project_key": project, "session_id": sid}

        await store.append(key, [_entry(uuid="e1")])
        await store.append(key, [_entry(uuid="e2")])

        recorded = await kurrentdb_client.get_stream(f"AgentSession-{sid}")
        types = [r.type for r in recorded]
        assert types.count("SessionStarted") == 1
        assert types.count("ClaudeSDKEntry") == 2
        # SessionStarted is the first event (§3.1 requires this).
        assert types[0] == "SessionStarted"


class TestSubpathParsing:
    """Unit tests for :func:`_subagent_id_from_subpath` — the edge cases
    Qodo flagged in the PR review: empty subpath, trailing slash, multi-
    segment path shouldn't collapse to an ambiguous or colliding agent_id.
    """

    def test_documented_shape_uses_final_segment(self) -> None:
        assert _subagent_id_from_subpath("subagents/agent-abc123") == "agent-abc123"

    def test_bare_agent_id_passes_through(self) -> None:
        assert _subagent_id_from_subpath("agent-abc123") == "agent-abc123"

    def test_trailing_slash_is_rejected(self) -> None:
        """A trailing slash after a known prefix would collapse agent_id to
        empty string and cause different subpaths to route to the same
        ``AgentSubsession-{sid}-`` stream."""
        with pytest.raises(ValueError, match="empty agent_id"):
            _subagent_id_from_subpath("subagents/")

    def test_empty_subpath_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty agent_id"):
            _subagent_id_from_subpath("")
        with pytest.raises(ValueError, match="empty agent_id"):
            _subagent_id_from_subpath("   ")

    def test_multi_segment_preserves_uniqueness(self) -> None:
        """``subagents/team/worker-1`` and ``subagents/other/worker-1`` must
        map to distinct ``agent_id``s so the transcripts don't collide."""
        a = _subagent_id_from_subpath("subagents/team/worker-1")
        b = _subagent_id_from_subpath("subagents/other/worker-1")
        assert a == "team_worker-1"
        assert b == "other_worker-1"
        assert a != b


class TestOptionalMethods:
    """Optional SessionStore methods must be **absent** on the class, not
    defined-but-raising.

    Per the SDK's Protocol contract: "implementers may omit them, and call
    sites probe for their presence at runtime before invoking". A
    defined-but-raising method is still "present" and the SDK surfaces the
    exception instead of falling back to its main-transcript-only path.
    """

    async def test_list_sessions_is_absent(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        store = KurrentDBSessionStore(kurrentdb_client)
        assert not hasattr(store, "list_sessions")

    async def test_delete_is_absent(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        store = KurrentDBSessionStore(kurrentdb_client)
        assert not hasattr(store, "delete")

    async def test_list_subkeys_is_absent(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        store = KurrentDBSessionStore(kurrentdb_client)
        assert not hasattr(store, "list_subkeys")
