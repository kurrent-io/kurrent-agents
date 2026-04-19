"""Integration tests for ``KurrentDBAgentMemory``."""

from __future__ import annotations

import uuid

from kurrentdbclient import KurrentDBClient

from kurrent_strands import KurrentDBAgentMemory


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return (f"app_{suffix}", f"user_{suffix}")


class TestRetainRecall:
    def test_empty_stream_returns_empty(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user = _ids()
        mem = KurrentDBAgentMemory(kurrentdb_client, app_name=app, user_id=user)
        assert mem.recall() == []

    def test_retain_then_recall_newest_first(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user = _ids()
        mem = KurrentDBAgentMemory(kurrentdb_client, app_name=app, user_id=user)
        for fact in ["fact one", "fact two", "fact three"]:
            mem.retain(fact)
        assert mem.recall() == ["fact three", "fact two", "fact one"]

    def test_query_is_ignored_in_v1(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user = _ids()
        mem = KurrentDBAgentMemory(kurrentdb_client, app_name=app, user_id=user)
        mem.retain("only fact")
        # Query text doesn't filter at the baseline — Kontext extra is future.
        assert mem.recall("completely unrelated") == ["only fact"]

    def test_empty_facts_are_dropped(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user = _ids()
        mem = KurrentDBAgentMemory(kurrentdb_client, app_name=app, user_id=user)
        mem.retain("")
        mem.retain("   ")
        mem.retain("real")
        assert mem.recall() == ["real"]

    def test_scoped_per_user(
        self, kurrentdb_client: KurrentDBClient
    ) -> None:
        app, user_a = _ids()
        _, user_b = _ids()
        mem_a = KurrentDBAgentMemory(kurrentdb_client, app_name=app, user_id=user_a)
        mem_b = KurrentDBAgentMemory(kurrentdb_client, app_name=app, user_id=user_b)
        mem_a.retain("A only")
        mem_b.retain("B only")
        assert mem_a.recall() == ["A only"]
        assert mem_b.recall() == ["B only"]
