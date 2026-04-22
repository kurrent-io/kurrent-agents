"""Integration tests for ``FactExtractionService``.

Ported from ``Kurrent.AgentFramework.IntegrationTests.FactExtractionServiceTests``
on the .NET side. Each test uses a unique subscription group name and an
extractor-scoped marker to stay parallel-safe against other tests sharing the
same KurrentDB instance.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from kurrent_agent_schema import (
    AssistantTextGenerated,
    SessionStarted,
    UserMessageReceived,
    agent_session_stream,
)
from kurrentdbclient import AsyncKurrentDBClient, StreamState

from kurrent_agent_framework import (
    FactExtractionOptions,
    FactExtractionService,
    run_fact_extraction,
    serialization,
)
from kurrent_agent_framework.memory import AgentMemory

_WAIT_BUDGET = 10.0
_TS = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


class InMemoryMemory:
    """Minimal ``AgentMemory`` implementation capturing retained facts."""

    def __init__(self) -> None:
        self._facts: list[str] = []
        self._lock = asyncio.Lock()

    async def recall(self, query: str) -> AsyncIterator[str]:  # pragma: no cover
        del query
        for fact in await self.snapshot():
            yield fact

    async def retain(self, fact: str) -> None:
        async with self._lock:
            self._facts.append(fact)

    async def snapshot(self) -> list[str]:
        async with self._lock:
            return list(self._facts)


def _unique_group() -> str:
    return f"FactExtraction-{uuid.uuid4().hex}"


async def _wait_until(predicate, timeout: float = _WAIT_BUDGET) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


async def _append(client: AsyncKurrentDBClient, stream: str, *events) -> None:
    await client.append_to_stream(
        stream,
        events=[serialization.serialize(e) for e in events],
        current_version=StreamState.ANY,
    )


async def _run_service(
    client: AsyncKurrentDBClient,
    memory: AgentMemory,
    extractor,
    options: FactExtractionOptions,
) -> tuple[FactExtractionService, asyncio.Task[None]]:
    service = FactExtractionService(client, memory, extractor, options)
    task = asyncio.create_task(service.run_forever())
    # Give the subscription a moment to register with the server before the
    # first append, otherwise the backfill-from-start behaviour races.
    await asyncio.sleep(0.2)
    return service, task


async def _stop(service: FactExtractionService, task: asyncio.Task[None]) -> None:
    service.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# -----------------------------------------------------------------------------


class TestFactExtraction:
    async def test_user_message_triggers_fact_retention(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        memory = InMemoryMemory()
        service, task = await _run_service(
            kurrentdb_client,
            memory,
            lambda content: [content] if marker in content else [],
            FactExtractionOptions(group_name=_unique_group()),
        )
        try:
            content = f"{marker} user fact content"
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content=content,
                    message_id="m-1",
                    author_name="user",
                    created_at=_TS,
                    message_index=0,
                    timestamp=_TS,
                ),
            )
            ok = await _wait_until(lambda: content in memory._facts)
            assert ok, f"fact not retained within {_WAIT_BUDGET}s"
        finally:
            await _stop(service, task)

    async def test_non_user_message_events_are_ignored(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        sentinel = f"{marker} sentinel"
        calls: list[str] = []
        memory = InMemoryMemory()

        def extractor(content: str) -> list[str]:
            calls.append(content)
            return [content] if marker in content else []

        service, task = await _run_service(
            kurrentdb_client,
            memory,
            extractor,
            FactExtractionOptions(group_name=_unique_group()),
        )
        try:
            await _append(
                kurrentdb_client,
                stream,
                SessionStarted(
                    agent_name=f"{marker} agent", model="model", timestamp=_TS
                ),
                AssistantTextGenerated(
                    content=f"{marker} assistant",
                    message_id="m-1",
                    author_name="agent",
                    created_at=_TS,
                    message_index=0,
                    timestamp=_TS,
                ),
                UserMessageReceived(
                    content=sentinel,
                    message_id="m-2",
                    author_name="user",
                    created_at=_TS,
                    message_index=1,
                    timestamp=_TS,
                ),
            )
            await _wait_until(lambda: sentinel in memory._facts)
            assert sentinel in memory._facts
            # Non-user events must never reach the extractor.
            assert not any("agent" in c for c in calls)
            assert not any("assistant" in c for c in calls)
        finally:
            await _stop(service, task)

    async def test_empty_user_messages_are_skipped(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        sentinel = f"{marker} sentinel"
        calls: list[str] = []
        memory = InMemoryMemory()

        def extractor(content: str) -> list[str]:
            calls.append(content)
            return [content] if marker in content else []

        service, task = await _run_service(
            kurrentdb_client,
            memory,
            extractor,
            FactExtractionOptions(group_name=_unique_group()),
        )
        try:
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content="", message_id="m-1", author_name="user",
                    created_at=_TS, message_index=0, timestamp=_TS,
                ),
                UserMessageReceived(
                    content="   ", message_id="m-2", author_name="user",
                    created_at=_TS, message_index=1, timestamp=_TS,
                ),
                UserMessageReceived(
                    content=sentinel, message_id="m-3", author_name="user",
                    created_at=_TS, message_index=2, timestamp=_TS,
                ),
            )
            await _wait_until(lambda: sentinel in memory._facts)
            assert sentinel in calls
            assert "" not in calls
            assert "   " not in calls
        finally:
            await _stop(service, task)

    async def test_events_outside_agent_session_prefix_are_filtered_server_side(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        marker = f"[M-{uuid.uuid4().hex}]"
        agent_stream = agent_session_stream(uuid.uuid4().hex)
        outside_stream = f"OtherStream-{uuid.uuid4().hex}"
        sentinel = f"{marker} sentinel"
        outside = f"{marker} outside"
        calls: list[str] = []
        memory = InMemoryMemory()

        def extractor(content: str) -> list[str]:
            calls.append(content)
            return [content] if marker in content else []

        service, task = await _run_service(
            kurrentdb_client,
            memory,
            extractor,
            FactExtractionOptions(group_name=_unique_group()),
        )
        try:
            await _append(
                kurrentdb_client,
                outside_stream,
                UserMessageReceived(
                    content=outside, message_id="m-1", author_name="user",
                    created_at=_TS, message_index=0, timestamp=_TS,
                ),
            )
            await _append(
                kurrentdb_client,
                agent_stream,
                UserMessageReceived(
                    content=sentinel, message_id="m-2", author_name="user",
                    created_at=_TS, message_index=0, timestamp=_TS,
                ),
            )
            await _wait_until(lambda: sentinel in memory._facts)
            # Give the subscription a moment to surface a mis-filtered event.
            await asyncio.sleep(0.5)
            assert outside not in memory._facts
            assert outside not in calls
        finally:
            await _stop(service, task)

    async def test_extractor_returning_no_facts_retains_nothing(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        memory = InMemoryMemory()
        service, task = await _run_service(
            kurrentdb_client,
            memory,
            lambda c: [c] if marker in c else [],
            FactExtractionOptions(group_name=_unique_group()),
        )
        try:
            non_matching = "unrelated content"
            sentinel = f"{marker} proof of life"
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content=non_matching, message_id="m-1", author_name="user",
                    created_at=_TS, message_index=0, timestamp=_TS,
                ),
                UserMessageReceived(
                    content=sentinel, message_id="m-2", author_name="user",
                    created_at=_TS, message_index=1, timestamp=_TS,
                ),
            )
            await _wait_until(lambda: sentinel in memory._facts)
            assert non_matching not in memory._facts
        finally:
            await _stop(service, task)

    async def test_whitespace_facts_from_extractor_are_not_retained(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        memory = InMemoryMemory()

        # Extractor returns a whitespace-only fact alongside a real one — the
        # service must drop the whitespace one.
        def extractor(content: str) -> list[str]:
            return ["   ", content] if marker in content else []

        service, task = await _run_service(
            kurrentdb_client,
            memory,
            extractor,
            FactExtractionOptions(group_name=_unique_group()),
        )
        try:
            content = f"{marker} real fact"
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content=content, message_id="m-1", author_name="user",
                    created_at=_TS, message_index=0, timestamp=_TS,
                ),
            )
            await _wait_until(lambda: content in memory._facts)
            assert "   " not in memory._facts
        finally:
            await _stop(service, task)

    async def test_run_fact_extraction_context_manager_lifecycle(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """``run_fact_extraction`` must spawn the task on enter and cancel it on exit."""
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        memory = InMemoryMemory()

        content = f"{marker} context-manager fact"
        async with run_fact_extraction(
            kurrentdb_client,
            memory,
            lambda c: [c] if marker in c else [],
            FactExtractionOptions(group_name=_unique_group()),
        ) as service:
            assert isinstance(service, FactExtractionService)
            # Subscription needs a beat to register before the first append.
            await asyncio.sleep(0.2)
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content=content,
                    message_id="m-1",
                    author_name="user",
                    created_at=_TS,
                    message_index=0,
                    timestamp=_TS,
                ),
            )
            assert await _wait_until(lambda: content in memory._facts)

        # After the context exits, the service must no longer be consuming.
        # Append another event and confirm the extractor is never invoked.
        post_exit = f"{marker} post-exit"
        await _append(
            kurrentdb_client,
            stream,
            UserMessageReceived(
                content=post_exit,
                message_id="m-2",
                author_name="user",
                created_at=_TS,
                message_index=1,
                timestamp=_TS,
            ),
        )
        await asyncio.sleep(0.5)
        assert post_exit not in memory._facts, (
            "Background task still processing events after context-manager exit."
        )

    async def test_run_fact_extraction_cancels_task_on_exception(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Exceptions inside the ``async with`` block must still tear down the task."""
        memory = InMemoryMemory()

        class _BoomError(Exception):
            pass

        with pytest.raises(_BoomError):
            async with run_fact_extraction(
                kurrentdb_client,
                memory,
                lambda c: [],
                FactExtractionOptions(group_name=_unique_group()),
            ):
                await asyncio.sleep(0.1)
                raise _BoomError

        # A leaked task would show up as a pending task referencing run_forever.
        leaked = [
            t
            for t in asyncio.all_tasks()
            if not t.done() and "run_forever" in (t.get_name() or "")
        ]
        assert not leaked, f"run_forever task leaked after exit: {leaked}"

    async def test_run_fact_extraction_propagates_outer_cancellation(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Cancelling the task that owns the ``async with`` must surface as
        CancelledError — the cleanup path must not swallow the caller's cancel.
        """
        memory = InMemoryMemory()
        entered = asyncio.Event()

        async def owner() -> None:
            async with run_fact_extraction(
                kurrentdb_client,
                memory,
                lambda _c: [],
                FactExtractionOptions(group_name=_unique_group()),
            ):
                entered.set()
                await asyncio.sleep(3600)

        task = asyncio.create_task(owner())
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_persistent_subscription_survives_service_restart(
        self, kurrentdb_client: AsyncKurrentDBClient
    ) -> None:
        """Second run with the same group must not replay previously acked events."""
        marker = f"[M-{uuid.uuid4().hex}]"
        stream = agent_session_stream(uuid.uuid4().hex)
        options = FactExtractionOptions(group_name=_unique_group())

        # First run — consume and ack the initial fact.
        first = f"{marker} first"
        memory_1 = InMemoryMemory()
        service_1, task_1 = await _run_service(
            kurrentdb_client,
            memory_1,
            lambda c: [c] if marker in c else [],
            options,
        )
        try:
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content=first, message_id="m-1", author_name="user",
                    created_at=_TS, message_index=0, timestamp=_TS,
                ),
            )
            await _wait_until(lambda: first in memory_1._facts)
            assert first in memory_1._facts
            # Allow the client to flush the ack to the server before stopping.
            await asyncio.sleep(0.5)
        finally:
            await _stop(service_1, task_1)

        # Second run with the same group — the server checkpoint must prevent
        # replay of `first` while still delivering a new event appended after
        # the restart.
        second = f"{marker} second"
        memory_2 = InMemoryMemory()
        service_2, task_2 = await _run_service(
            kurrentdb_client,
            memory_2,
            lambda c: [c] if marker in c else [],
            options,
        )
        try:
            await _append(
                kurrentdb_client,
                stream,
                UserMessageReceived(
                    content=second, message_id="m-2", author_name="user",
                    created_at=_TS, message_index=1, timestamp=_TS,
                ),
            )
            await _wait_until(lambda: second in memory_2._facts)
            assert second in memory_2._facts
            assert first not in memory_2._facts, (
                "Persistent subscription replayed `first` after restart — "
                "server-side checkpointing is broken."
            )
        finally:
            await _stop(service_2, task_2)
