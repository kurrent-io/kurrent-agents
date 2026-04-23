"""Integration tests for the KurrentDB workflow integration.

Exercises :class:`KurrentDBCheckpointStorage` (:class:`CheckpointStorage`
protocol) and :class:`KurrentDBGroupChatRecorder` against a real KurrentDB
container. Ported from the .NET ``KurrentDBCheckpointStoreTests`` and
``KurrentDBGroupChatManager`` coverage.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from agent_framework import WorkflowCheckpoint, WorkflowEvent
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_agent_framework import (
    AgentTurnTaken,
    KurrentDBCheckpointStorage,
    KurrentDBGroupChatRecorder,
    group_chat_stream,
    workflow_checkpoint_stream,
)


def _workflow_name(prefix: str = "wf") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _new_checkpoint(
    workflow_name: str,
    *,
    iteration: int = 0,
    previous: str | None = None,
    state: dict[str, Any] | None = None,
) -> WorkflowCheckpoint:
    return WorkflowCheckpoint(
        workflow_name=workflow_name,
        graph_signature_hash="deadbeef",
        previous_checkpoint_id=previous,
        iteration_count=iteration,
        state=state or {"step": iteration},
    )


class _StaticEvents:
    """Minimal async-iterable of workflow events for recorder tests."""

    def __init__(self, events: list[WorkflowEvent[Any]]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[WorkflowEvent[Any]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[WorkflowEvent[Any]]:
        for event in self._events:
            yield event


# --- KurrentDBCheckpointStorage --------------------------------------------


async def test_save_returns_checkpoint_id_and_scopes_by_workflow_name(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()
    cp = _new_checkpoint(wf, iteration=1)

    returned_id = await store.save(cp)

    assert returned_id == cp.checkpoint_id

    # The stream name is derived from the workflow name, not the checkpoint id.
    response = await kurrentdb_client.read_stream(stream_name=workflow_checkpoint_stream(wf))
    recorded = [ev async for ev in response]
    assert len(recorded) == 1
    assert recorded[0].type == "WorkflowCheckpoint"


async def test_list_checkpoints_returns_every_checkpoint_for_workflow(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()

    a = _new_checkpoint(wf, iteration=1)
    b = _new_checkpoint(wf, iteration=2)
    c = _new_checkpoint(wf, iteration=3)
    for cp in (a, b, c):
        await store.save(cp)

    listed = await store.list_checkpoints(workflow_name=wf)

    assert {cp.checkpoint_id for cp in listed} == {a.checkpoint_id, b.checkpoint_id, c.checkpoint_id}
    assert {cp.iteration_count for cp in listed} == {1, 2, 3}


async def test_list_checkpoint_ids_skips_payload_decode(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()
    saved_ids = []
    for i in range(3):
        cp = _new_checkpoint(wf, iteration=i)
        saved_ids.append(await store.save(cp))

    ids = await store.list_checkpoint_ids(workflow_name=wf)

    assert set(ids) == set(saved_ids)


async def test_get_latest_returns_most_recently_appended(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()
    first = _new_checkpoint(wf, iteration=1)
    second = _new_checkpoint(wf, iteration=2, previous=first.checkpoint_id)
    third = _new_checkpoint(wf, iteration=3, previous=second.checkpoint_id)
    for cp in (first, second, third):
        await store.save(cp)

    latest = await store.get_latest(workflow_name=wf)

    assert latest is not None
    assert latest.checkpoint_id == third.checkpoint_id
    assert latest.iteration_count == 3
    assert latest.previous_checkpoint_id == second.checkpoint_id


async def test_get_latest_returns_none_for_unknown_workflow(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)

    assert await store.get_latest(workflow_name=_workflow_name("never-saved")) is None


async def test_list_checkpoints_empty_for_unknown_workflow(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)

    assert await store.list_checkpoints(workflow_name=_workflow_name("never-saved")) == []


async def test_checkpoints_are_isolated_per_workflow_name(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf_a = _workflow_name("a")
    wf_b = _workflow_name("b")
    a = _new_checkpoint(wf_a, iteration=1)
    b = _new_checkpoint(wf_b, iteration=1)
    await store.save(a)
    await store.save(b)

    listed = await store.list_checkpoints(workflow_name=wf_a)

    assert [cp.checkpoint_id for cp in listed] == [a.checkpoint_id]


async def test_save_roundtrips_complex_state(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()
    complex_state: dict[str, Any] = {
        "counter": 7,
        "items": ["x", "y", "z"],
        "inner": {"name": "deep", "flag": True},
    }
    cp = _new_checkpoint(wf, iteration=1, state=complex_state)
    await store.save(cp)

    latest = await store.get_latest(workflow_name=wf)

    assert latest is not None
    assert latest.state == complex_state


async def test_load_returns_checkpoint_without_knowing_workflow_name(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """The ``CheckpointStorage.load`` protocol signature is ``load(checkpoint_id)``,
    so the store must locate the event without being told the workflow name.
    Uses the built-in ``$ce-WorkflowCheckpoint`` category projection."""
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()
    cp = _new_checkpoint(wf, iteration=5, state={"marker": "findable"})
    await store.save(cp)

    loaded = await store.load(cp.checkpoint_id)

    assert loaded.checkpoint_id == cp.checkpoint_id
    assert loaded.workflow_name == wf
    assert loaded.state == {"marker": "findable"}


async def test_delete_returns_false_and_is_noop(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """KurrentDB streams are append-only; delete is intentionally unsupported."""
    store = KurrentDBCheckpointStorage(kurrentdb_client)
    wf = _workflow_name()
    cp = _new_checkpoint(wf, iteration=1)
    await store.save(cp)

    removed = await store.delete(cp.checkpoint_id)

    assert removed is False
    # The event is still there.
    still_there = await store.list_checkpoint_ids(workflow_name=wf)
    assert still_there == [cp.checkpoint_id]


# --- KurrentDBGroupChatRecorder --------------------------------------------


def _group_chat_response_event(participant: str, round_index: int) -> WorkflowEvent[Any]:
    """Build a WorkflowEvent shaped like what ``GroupChatOrchestrator`` emits.

    ``agent_framework_orchestrations`` is pulled in as a ``[dev]`` extra —
    tests that use this helper require ``pip install -e ".[dev]"``.
    """
    from agent_framework_orchestrations import GroupChatResponseReceivedEvent

    return WorkflowEvent(
        "group_chat",
        data=GroupChatResponseReceivedEvent(round_index=round_index, participant_name=participant),
    )


async def test_recorder_writes_turn_per_group_chat_response(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    chat_id = uuid.uuid4().hex
    recorder = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)

    stream: list[WorkflowEvent[Any]] = [
        _group_chat_response_event("writer", 0),
        _group_chat_response_event("reviewer", 1),
        _group_chat_response_event("writer", 2),
    ]

    forwarded = [ev async for ev in recorder.record(_StaticEvents(stream), completion_reason="done")]
    assert len(forwarded) == 3  # record() re-yields every input event

    history = await recorder.read_history()

    assert [t.participant_name for t in history] == ["writer", "reviewer", "writer"]
    assert [t.round_index for t in history] == [0, 1, 2]


async def test_recorder_ignores_request_sent_events(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """Only ``GroupChatResponseReceivedEvent`` counts as a turn — otherwise each
    round would be double-counted against the matching request-sent event."""
    from agent_framework_orchestrations import (
        GroupChatRequestSentEvent,
        GroupChatResponseReceivedEvent,
    )

    chat_id = uuid.uuid4().hex
    recorder = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)

    stream: list[WorkflowEvent[Any]] = [
        WorkflowEvent("group_chat", data=GroupChatRequestSentEvent(round_index=0, participant_name="writer")),
        WorkflowEvent("group_chat", data=GroupChatResponseReceivedEvent(round_index=0, participant_name="writer")),
    ]

    async for _ in recorder.record(_StaticEvents(stream)):
        pass

    history = await recorder.read_history()

    assert len(history) == 1
    assert history[0].participant_name == "writer"


async def test_recorder_falls_back_to_executor_completed_for_non_group_chat(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """Sequential/Concurrent/custom workflows don't emit ``group_chat`` events.
    The recorder treats every ``executor_completed`` as a participant turn so
    the same recorder class works against any orchestration pattern."""
    chat_id = uuid.uuid4().hex
    recorder = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)

    stream: list[WorkflowEvent[Any]] = [
        WorkflowEvent.executor_invoked("writer"),  # ignored — only *_completed counts
        WorkflowEvent.executor_completed("writer"),
        WorkflowEvent.executor_completed("reviewer"),
    ]

    async for _ in recorder.record(_StaticEvents(stream)):
        pass

    history = await recorder.read_history()

    assert [t.participant_name for t in history] == ["writer", "reviewer"]
    assert [t.round_index for t in history] == [0, 1]


async def test_recorder_writes_completion_event_after_stream_ends(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    chat_id = uuid.uuid4().hex
    recorder = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)

    stream: list[WorkflowEvent[Any]] = [
        _group_chat_response_event("writer", 0),
        _group_chat_response_event("reviewer", 1),
    ]

    async for _ in recorder.record(_StaticEvents(stream), completion_reason="max-rounds"):
        pass

    # Read every event on the chat stream to confirm a GroupChatCompleted got appended.
    response = await kurrentdb_client.read_stream(stream_name=group_chat_stream(chat_id))
    types = [ev.type async for ev in response]

    assert types == ["AgentTurnTaken", "AgentTurnTaken", "GroupChatCompleted"]


async def test_read_history_returns_empty_for_unknown_chat(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    recorder = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=uuid.uuid4().hex)

    history = await recorder.read_history()

    assert isinstance(history, list)
    assert history == []


async def test_recorder_resumes_round_counter_across_restarts(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    """When only fallback (``executor_completed``) events drive the counter, a
    second recorder for the same ``chat_id`` must continue numbering from the
    last stored turn rather than starting back at 0."""
    chat_id = uuid.uuid4().hex

    first = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)
    async for _ in first.record(
        _StaticEvents([WorkflowEvent.executor_completed("writer"), WorkflowEvent.executor_completed("reviewer")])
    ):
        pass

    second = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)
    async for _ in second.record(_StaticEvents([WorkflowEvent.executor_completed("editor")])):
        pass

    history = await second.read_history()

    assert [t.round_index for t in history] == [0, 1, 2]
    assert [t.participant_name for t in history] == ["writer", "reviewer", "editor"]


async def test_stream_name_builders_reject_blank_identifiers() -> None:
    """Empty / whitespace-only ids would silently collapse independent
    workflows or chats into a shared stream — same guard as memory.py."""
    import pytest

    with pytest.raises(ValueError):
        workflow_checkpoint_stream("")
    with pytest.raises(ValueError):
        workflow_checkpoint_stream("   ")
    with pytest.raises(ValueError):
        group_chat_stream("")
    with pytest.raises(ValueError):
        group_chat_stream("\t")


async def test_read_history_round_trip_preserves_turn_fields(
    kurrentdb_client: AsyncKurrentDBClient,
) -> None:
    chat_id = uuid.uuid4().hex
    recorder = KurrentDBGroupChatRecorder(kurrentdb_client, chat_id=chat_id)

    stream: list[WorkflowEvent[Any]] = [_group_chat_response_event("writer", 0)]
    async for _ in recorder.record(_StaticEvents(stream)):
        pass

    history = await recorder.read_history()

    assert len(history) == 1
    turn = history[0]
    assert isinstance(turn, AgentTurnTaken)
    assert turn.participant_name == "writer"
    assert turn.round_index == 0
    # ``timestamp`` is parseable and within a sane recent window.
    assert turn.timestamp is not None
