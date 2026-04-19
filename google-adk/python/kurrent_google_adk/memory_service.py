"""``KurrentDBMemoryService`` — see ``DESIGN.md`` §7.2.

Implements ``google.adk.memory.BaseMemoryService`` backed by the canonical
``AgentMemory-{app}-{user}`` stream (``SCHEMA.md`` §3.6). Each retained entry
becomes a canonical ``FactRetained`` event; ADK's richer ``MemoryEntry``
metadata (``author``, ``id``, ``timestamp``, ``custom_metadata``) rides in
``extensions.adk`` so same-framework reads round-trip losslessly while a
cross-framework reader still sees the fact text.

Baseline ``search_memory`` returns every retained entry, matching the .NET
``KurrentDBAgentMemory`` contract. For hybrid BM25 + vector retrieval,
use the Kontext-backed implementation (not in v1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types
from kurrentdbclient import StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from ._schema import events as _events
from ._schema.events import ADK_EXTENSION_KEY
from ._schema.stream_names import for_memory

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.events.event import Event
    from google.adk.sessions.session import Session
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBMemoryService(BaseMemoryService):
    """KurrentDB-backed memory service (per-app, per-user scope).

    All writes land in ``AgentMemory-{app_name}-{user_id}`` as canonical
    ``FactRetained`` events. Wider scopes (``AgentMemory-{app_name}``,
    ``AgentMemory``) are reserved by the schema but not supported in v1 —
    deliberate encapsulation to prevent cross-user memory leakage by default.
    """

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client

    async def add_session_to_memory(self, session: Session) -> None:
        """Retain every textful event from ``session`` as a separate entry.

        A simple default — treat every user/assistant text message as a fact.
        Callers that want more selective retention should use
        :meth:`add_events_to_memory` or :meth:`add_memory` directly.
        """
        entries = [
            _fact_from_event(event, session_id=session.id)
            for event in session.events
        ]
        entries = [e for e in entries if e is not None]
        if entries:
            await self._append(session.app_name, session.user_id, entries)

    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: Sequence[Event],
        session_id: str | None = None,
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Incremental retention — same mapping rules as ``add_session_to_memory``."""
        extra = dict(custom_metadata) if custom_metadata else None
        entries = [
            _fact_from_event(event, session_id=session_id, extra=extra)
            for event in events
        ]
        entries = [e for e in entries if e is not None]
        if entries:
            await self._append(app_name, user_id, entries)

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Direct memory writes — one ``FactRetained`` per non-empty ``MemoryEntry``."""
        extra = dict(custom_metadata) if custom_metadata else None
        entries = [
            _fact_from_memory_entry(mem, extra=extra) for mem in memories
        ]
        entries = [e for e in entries if e is not None]
        if entries:
            await self._append(app_name, user_id, entries)

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """Baseline: return every retained entry, newest-first."""
        del query  # ignored — baseline returns all
        stream = for_memory(app_name, user_id)
        try:
            recorded = await self._client.get_stream(stream, backwards=True)
        except NotFoundError:
            return SearchMemoryResponse(memories=[])

        memories: list[MemoryEntry] = []
        for record in recorded:
            canonical = _serialization.deserialize(record)
            if not isinstance(canonical, _events.FactRetained):
                continue
            entry = _memory_entry_from_fact(canonical)
            if entry is not None:
                memories.append(entry)
        return SearchMemoryResponse(memories=memories)

    async def _append(
        self,
        app_name: str,
        user_id: str,
        entries: list[_events.FactRetained],
    ) -> None:
        stream = for_memory(app_name, user_id)
        new_events = [_serialization.serialize(e) for e in entries]
        await self._client.append_to_stream(
            stream,
            events=new_events,
            current_version=StreamState.ANY,
        )


# ----- helpers ---------------------------------------------------------------


def _event_text(event: Event) -> str | None:
    """Concatenate text parts on an ADK Event; return ``None`` if empty."""
    if not event.content or not event.content.parts:
        return None
    chunks = [p.text for p in event.content.parts if p.text]
    text = "".join(chunks).strip()
    return text or None


def _content_text(content: types.Content | None) -> str | None:
    if content is None or not content.parts:
        return None
    chunks = [p.text for p in content.parts if p.text]
    text = "".join(chunks).strip()
    return text or None


def _fact_from_event(
    event: Event,
    *,
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> _events.FactRetained | None:
    text = _event_text(event)
    if text is None:
        return None
    ext: dict[str, Any] = {}
    if event.author:
        ext["author"] = event.author
    if event.id:
        ext["event_id"] = event.id
    if event.invocation_id:
        ext["invocation_id"] = event.invocation_id
    if session_id:
        ext["session_id"] = session_id
    if extra:
        ext["custom_metadata"] = extra
    return _events.FactRetained(
        fact=text,
        retained_at=datetime.now(UTC),
        extensions={ADK_EXTENSION_KEY: ext} if ext else None,
    )


def _fact_from_memory_entry(
    entry: MemoryEntry,
    *,
    extra: dict[str, Any] | None = None,
) -> _events.FactRetained | None:
    text = _content_text(entry.content)
    if text is None:
        return None
    ext: dict[str, Any] = {}
    if entry.id:
        ext["memory_id"] = entry.id
    if entry.author:
        ext["author"] = entry.author
    if entry.timestamp:
        ext["source_timestamp"] = entry.timestamp
    if entry.custom_metadata:
        ext["custom_metadata"] = dict(entry.custom_metadata)
    if extra:
        ext["source_metadata"] = extra
    return _events.FactRetained(
        fact=text,
        retained_at=datetime.now(UTC),
        extensions={ADK_EXTENSION_KEY: ext} if ext else None,
    )


def _memory_entry_from_fact(fact: _events.FactRetained) -> MemoryEntry | None:
    if not fact.fact.strip():
        return None
    ext = (fact.extensions or {}).get(ADK_EXTENSION_KEY, {})
    author = ext.get("author")
    # Prefer the original event's author role when reconstructing Content —
    # fall back to "user" since google.genai accepts only "user" or "model".
    content_role = "user" if author == "user" else "model"
    return MemoryEntry(
        content=types.Content(
            role=content_role, parts=[types.Part(text=fact.fact)]
        ),
        custom_metadata=dict(ext.get("custom_metadata") or {}),
        id=ext.get("memory_id"),
        author=author,
        timestamp=ext.get("source_timestamp") or fact.retained_at.isoformat(),
    )
