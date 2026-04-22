"""Event-sourced chat history backed by KurrentDB.

Each ``Message`` is decomposed into typed canonical events on save and
reconstructed on load. Session lifecycle events (``SessionStarted`` /
``SessionEnded``) frame each stream. Event types come from the shared
:mod:`kurrent_agent_schema` package so the write/read path stays byte-identical
to the MAF .NET mirror.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from agent_framework import Content, HistoryProvider, Message
from kurrent_agent_schema import (
    AssistantTextGenerated,
    AssistantToolCallsGenerated,
    SessionEnded,
    SessionStarted,
    ToolCallInfo,
    ToolResultReceived,
    UserMessageReceived,
    agent_session_stream,
)
from kurrent_agent_schema.events import _EventBase
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import serialization


class KurrentDBHistoryProvider(HistoryProvider):
    """Persists chat history as rich typed events in KurrentDB.

    Each stored message is decomposed into one or more canonical events
    (``UserMessageReceived``, ``AssistantTextGenerated``,
    ``AssistantToolCallsGenerated``, ``ToolResultReceived``) and reconstructed
    on read.

    A ``SessionStarted`` event is emitted as the first write to a new stream.
    Call :meth:`end_session` to append a ``SessionEnded`` event when the
    conversation is complete.

    Args:
        client: Async KurrentDB client.
        source_id: Unique identifier for this provider (used by the context pipeline).
        agent_name: Recorded in the ``SessionStarted`` event. Optional.
        model_name: Recorded in the ``SessionStarted`` event. Optional.
        app_name: Recorded in the ``SessionStarted`` event. Optional. New in v2.
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        source_id: str = "kurrentdb_history",
        agent_name: str | None = None,
        model_name: str | None = None,
        app_name: str | None = None,
    ) -> None:
        super().__init__(source_id)
        self._client = client
        self._agent_name = agent_name
        self._model_name = model_name
        self._app_name = app_name
        self._started_sessions: set[str] = set()

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        if not session_id:
            return []

        stream = agent_session_stream(session_id)
        messages: list[Message] = []

        try:
            response = await self._client.read_stream(stream)
            async for recorded in response:
                self._started_sessions.add(session_id)
                event = serialization.deserialize(recorded)
                if event is None:
                    continue
                msg = _event_to_message(event)
                if msg is not None:
                    messages.append(msg)
        except NotFoundError:
            # First interaction — no history yet.
            pass

        return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not session_id or not messages:
            return

        stream = agent_session_stream(session_id)
        now = datetime.now(UTC)
        to_append = []

        if session_id not in self._started_sessions:
            to_append.append(
                serialization.serialize(
                    SessionStarted(
                        app_name=self._app_name,
                        agent_name=self._agent_name,
                        model=self._model_name,
                        timestamp=now,
                    )
                )
            )
            self._started_sessions.add(session_id)

        for index, message in enumerate(messages):
            for event in _message_to_events(message, message_index=index, timestamp=now):
                to_append.append(serialization.serialize(event))

        if to_append:
            await self._client.append_to_stream(
                stream_name=stream,
                current_version=StreamState.ANY,
                events=to_append,
            )

    async def end_session(self, session_id: str, reason: str | None = None) -> None:
        """Append a ``SessionEnded`` event to close the session stream."""
        stream = agent_session_stream(session_id)
        await self._client.append_to_stream(
            stream_name=stream,
            current_version=StreamState.ANY,
            events=[
                serialization.serialize(
                    SessionEnded(
                        reason=reason,
                        timestamp=datetime.now(UTC),
                    )
                )
            ],
        )


# --- Message <-> event conversion -------------------------------------------


def _message_to_events(
    message: Message,
    *,
    message_index: int,
    timestamp: datetime,
) -> Iterable[_EventBase]:
    """Decompose a ``Message`` into one or more canonical events."""
    msg_id = message.message_id
    author = message.author_name
    role = message.role

    if role == "user":
        yield UserMessageReceived(
            content=message.text,
            message_id=msg_id,
            author_name=author,
            message_index=message_index,
            timestamp=timestamp,
        )
        return

    if role == "assistant":
        tool_calls = [
            ToolCallInfo(
                call_id=c.call_id or "",
                tool_name=c.name or "",
                arguments=_coerce_arguments(c.arguments),
            )
            for c in message.contents
            if c.type == "function_call"
        ]
        if tool_calls:
            yield AssistantToolCallsGenerated(
                tool_calls=tool_calls,
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        else:
            yield AssistantTextGenerated(
                content=message.text,
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )
        return

    if role == "tool":
        for c in message.contents:
            if c.type != "function_result":
                continue
            yield ToolResultReceived(
                call_id=c.call_id or "",
                tool_name=None,
                result=_coerce_result(c.result),
                message_id=msg_id,
                author_name=author,
                message_index=message_index,
                timestamp=timestamp,
            )


def _event_to_message(event: _EventBase) -> Message | None:
    """Reconstruct a ``Message`` from a canonical event, or ``None`` for lifecycle events."""
    if isinstance(event, UserMessageReceived):
        return Message(
            role="user",
            contents=[Content(type="text", text=event.content or "")],
            message_id=event.message_id,
            author_name=event.author_name,
        )
    if isinstance(event, AssistantTextGenerated):
        return Message(
            role="assistant",
            contents=[Content(type="text", text=event.content or "")],
            message_id=event.message_id,
            author_name=event.author_name,
        )
    if isinstance(event, AssistantToolCallsGenerated):
        contents: list[Content] = []
        if event.content:
            contents.append(Content(type="text", text=event.content))
        contents.extend(
            Content(
                type="function_call",
                call_id=tc.call_id,
                name=tc.tool_name,
                arguments=tc.arguments,
            )
            for tc in event.tool_calls
        )
        return Message(
            role="assistant",
            contents=contents,
            message_id=event.message_id,
            author_name=event.author_name,
        )
    if isinstance(event, ToolResultReceived):
        return Message(
            role="tool",
            contents=[Content(type="function_result", call_id=event.call_id, result=event.result)],
            message_id=event.message_id,
            author_name=event.author_name,
        )
    return None


def _coerce_arguments(arguments: Any) -> dict[str, Any] | None:
    if arguments is None:
        return None
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _coerce_result(result: Any) -> str | None:
    if result is None:
        return None
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return str(result)
