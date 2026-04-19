"""``KurrentDBSessionStore`` — adapter for the Claude Agent SDK's ``SessionStore`` protocol.

The SDK writes every JSONL transcript line to the local ``CLAUDE_CONFIG_DIR``
first; this adapter receives each batch *post-hoc* and mirrors the entries to
KurrentDB. Per the SDK docs, entries are "pass-through blobs; round-tripping
``json.dumps`` / ``json.loads`` is the only required invariant" — so v0
preserves each entry verbatim inside one ``ClaudeSDKEntry`` canonical event.

Required SessionStore methods implemented here: ``append``, ``load``.
Optional methods (``list_sessions``, ``delete``, ``list_subkeys``) raise
``NotImplementedError`` at the protocol level — the SDK probes for their
presence at runtime and skips them when absent. A follow-up can wire these
to KurrentDB system streams or a category projection.

Stream layout: one stream per ``SessionKey``.

- Main transcript: ``AgentSession-{session_id}``.
- Subagent transcript: ``AgentSession-{session_id}__{normalised_subpath}``.
  (Subpath characters outside ``[a-zA-Z0-9_.]`` are percent-encoded, and ``/``
  is mapped to ``_`` so subagent transcripts land in their own stream.)

``project_key`` is stashed on the ``SessionStarted`` marker and on every
``ClaudeSDKEntry`` extension for traceability but doesn't scope the stream
name in v0 — the SDK ensures ``session_id`` uniqueness per project.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from ._schema import events as _events
from ._schema.events import CLAUDE_SDK_EXTENSION_KEY
from ._schema.stream_names import _normalise_id, for_session

if TYPE_CHECKING:  # pragma: no cover
    from claude_agent_sdk import (
        SessionKey,
        SessionListSubkeysKey,
        SessionStoreEntry,
        SessionStoreListEntry,
    )

logger = logging.getLogger("kurrent_claude_agent_sdk.session_store")


class KurrentDBSessionStore:
    """KurrentDB-backed ``SessionStore`` adapter for the Claude Agent SDK.

    Args:
        client: Async KurrentDB client.
        app_name: Optional application identifier for observability. The SDK
            has no native app/user concept; supply it as integration config
            to scope future memory / artifact integrations.
        user_id: Same treatment as ``app_name``.
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        app_name: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._client = client
        self._app_name = app_name
        self._user_id = user_id
        # Cache of session streams we've already written SessionStarted to
        # (per process; cross-process appends race but the first writer wins).
        self._started_streams: set[str] = set()

    # ----- required methods --------------------------------------------------

    async def append(
        self, key: SessionKey, entries: list[SessionStoreEntry]
    ) -> None:
        """Mirror a batch of CLI JSONL transcript entries to KurrentDB.

        Called *after* the local write succeeds; at-most-once — failed batches
        are logged but not retried, per the SDK contract. Exceptions must not
        propagate (the subprocess keeps running).
        """
        if not entries:
            return
        try:
            stream = self._stream_for(key)
            subpath = key.get("subpath")
            await self._ensure_session_started(stream, key)

            new_events = [
                _serialization.serialize(
                    _events.ClaudeSDKEntry(
                        entry_type=str(entry.get("type") or ""),
                        entry_uuid=str(entry.get("uuid") or ""),
                        entry_timestamp=str(entry.get("timestamp") or ""),
                        raw_entry=dict(entry),
                        subpath=subpath,
                        timestamp=datetime.now(UTC),
                        extensions={
                            CLAUDE_SDK_EXTENSION_KEY: {
                                "project_key": key.get("project_key"),
                            }
                        },
                    )
                )
                for entry in entries
            ]
            await self._client.append_to_stream(
                stream,
                events=new_events,
                current_version=StreamState.ANY,
            )
        except Exception:
            # Swallow + log per SDK contract: "Exceptions are logged; the
            # subprocess continues unaffected. At-most-once delivery".
            logger.exception(
                "KurrentDBSessionStore.append failed for session %r (subpath=%r); "
                "%d entries dropped.",
                key.get("session_id"),
                key.get("subpath"),
                len(entries),
            )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        """Load every transcript entry for a session, newest-last.

        Returns ``None`` for a key we've never seen — the SDK treats ``None``
        as "no such session", prompting a fresh subprocess spawn.
        """
        stream = self._stream_for(key)
        try:
            recorded = await self._client.get_stream(stream)
        except NotFoundError:
            return None

        entries: list[SessionStoreEntry] = []
        for record in recorded:
            event = _serialization.deserialize(record)
            if not isinstance(event, _events.ClaudeSDKEntry):
                continue
            # Only entries matching this key's subpath (or lack thereof) belong
            # in the result; a session stream may contain both main + subagent
            # subkeys from future revisions.
            if event.subpath != key.get("subpath"):
                continue
            entries.append(event.raw_entry)  # type: ignore[arg-type]
        return entries or None

    # ----- optional methods (unimplemented in v0) ----------------------------

    async def list_sessions(
        self, project_key: str
    ) -> list[SessionStoreListEntry]:
        raise NotImplementedError(
            "list_sessions requires a category projection on $ce-AgentSession; "
            "follow-up."
        )

    async def delete(self, key: SessionKey) -> None:
        raise NotImplementedError(
            "delete is a no-op for append-only storage; follow-up adds an "
            "explicit tombstone event for callers that need it."
        )

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        raise NotImplementedError(
            "list_subkeys will come with the subagent-discovery pass; v0 only "
            "materialises the main transcript on resume."
        )

    # ----- internals ---------------------------------------------------------

    def _stream_for(self, key: SessionKey) -> str:
        session_id = key["session_id"]
        base = for_session(session_id)
        subpath = key.get("subpath")
        if not subpath:
            return base
        # Subagent transcripts land in their own stream — ``/``-separated
        # subpath flattened to a safe suffix.
        normalised = _normalise_id(subpath.replace("/", "_"), field="subpath")
        return f"{base}__{normalised}"

    async def _ensure_session_started(
        self, stream: str, key: SessionKey
    ) -> None:
        """Write ``SessionStarted`` on first touch for a stream — idempotent."""
        if stream in self._started_streams:
            return
        started = _events.SessionStarted(
            app_name=self._app_name,
            user_id=self._user_id,
            timestamp=datetime.now(UTC),
            extensions={
                CLAUDE_SDK_EXTENSION_KEY: {
                    "project_key": key.get("project_key"),
                    "session_id": key.get("session_id"),
                    "subpath": key.get("subpath"),
                }
            },
        )
        try:
            await self._client.append_to_stream(
                stream,
                events=[_serialization.serialize(started)],
                current_version=StreamState.NO_STREAM,
            )
        except Exception:
            # Stream already exists — another append got there first. Benign.
            pass
        self._started_streams.add(stream)
