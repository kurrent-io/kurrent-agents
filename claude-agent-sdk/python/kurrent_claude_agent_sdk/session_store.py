"""``KurrentDBSessionStore`` — adapter for the Claude Agent SDK's ``SessionStore`` protocol.

The SDK writes every JSONL transcript line to the local ``CLAUDE_CONFIG_DIR``
first; this adapter receives each batch *post-hoc* and mirrors the entries to
KurrentDB. Per the SDK docs, entries are "pass-through blobs; round-tripping
``json.dumps`` / ``json.loads`` is the only required invariant" — so entries
are preserved verbatim inside one ``ClaudeSDKEntry`` event per JSONL line.

Required SessionStore methods implemented here: ``append``, ``load``.
Optional methods (``list_sessions``, ``delete``, ``list_subkeys``,
``list_session_summaries``) are **absent from this class**, not defined-but-
raising. The SDK probes for their presence via ``hasattr`` at runtime and
skips them when missing; a defined-but-raising method would be "present" and
the SDK would surface the exception instead of falling back to its built-in
default path.

Stream layout (schema v2):

- Main transcript: ``AgentSession-{session_id}``.
- Subagent transcript: ``AgentSubsession-{session_id}-{agent_id}`` where
  ``agent_id`` is the final path component of the SDK's
  ``SessionKey.subpath`` (e.g. ``subagents/agent-abc123`` → ``agent-abc123``).
  Per SCHEMA_v2 §3.5, subagent streams do **not** carry their own
  ``SessionStarted`` — that role is fulfilled by ``SubagentStarted`` on the
  parent stream, which this adapter does not emit today (the SDK's
  ``SessionStore`` protocol doesn't expose subagent lifecycle signals).

``project_key`` is stashed on ``SessionStarted`` and on every
``ClaudeSDKEntry``'s ``extensions.claude_sdk.project_key`` for traceability
but doesn't scope the stream name — the SDK guarantees ``session_id``
uniqueness per project.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kurrent_agent_schema import SessionStarted
from kurrent_agent_schema.streams import agent_session_stream, agent_subsession_stream
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from .events import CLAUDE_SDK_EXTENSION_KEY, ClaudeSDKEntry

if TYPE_CHECKING:  # pragma: no cover
    from claude_agent_sdk import SessionKey, SessionStoreEntry

logger = logging.getLogger("kurrent_claude_agent_sdk.session_store")


def _subagent_id_from_subpath(subpath: str) -> str:
    """Extract an ``agent_id`` from the SDK's ``SessionKey.subpath``.

    The SDK documents ``subpath`` as an opaque ``/``-separated string; the
    observed shape is ``subagents/agent-{id}``. We take the final path segment
    so the resulting stream name stays a clean ``AgentSubsession-{session}-{id}``
    (no embedded slashes). Paths without slashes are used as-is.
    """
    return subpath.rsplit("/", 1)[-1]


class KurrentDBSessionStore:
    """KurrentDB-backed ``SessionStore`` adapter for the Claude Agent SDK.

    Args:
        client: Async KurrentDB client.
        app_name: Optional application identifier. Recorded on ``SessionStarted``
            for the main transcript. The SDK has no native app/user concept;
            supply these to scope downstream memory / artifact integrations.
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
        # Per-process cache of streams we've already written SessionStarted to.
        # Cross-process races are benign: the second writer's NO_STREAM append
        # fails, we log+swallow, and move on.
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
                    ClaudeSDKEntry(
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
            if not isinstance(event, ClaudeSDKEntry):
                continue
            # Post-v2 subagent streams are scoped by name, so there is no
            # mixed-subpath traffic on a stream. We still gate on the
            # recorded ``subpath`` so pre-cutover streams (v1 layout with
            # mixed subkeys) keep returning the right slice.
            if event.subpath != key.get("subpath"):
                continue
            entries.append(event.raw_entry)  # type: ignore[arg-type]
        return entries or None

    # ----- optional SessionStore methods -------------------------------------
    #
    # ``list_sessions``, ``delete``, ``list_subkeys``, ``list_session_summaries``
    # are deliberately **absent** from this class. The SDK probes for the
    # methods' presence at runtime (see Protocol docstring: "implementers may
    # omit them, and call sites probe for their presence before invoking"). A
    # method that's defined-but-raises is still "present" and the SDK surfaces
    # the error — for true "unimplemented" semantics the methods must not
    # exist on the object at all. See commit 6a33c21.

    # ----- internals ---------------------------------------------------------

    def _stream_for(self, key: SessionKey) -> str:
        session_id = key["session_id"]
        subpath = key.get("subpath")
        if not subpath:
            return agent_session_stream(session_id)
        return agent_subsession_stream(session_id, _subagent_id_from_subpath(subpath))

    async def _ensure_session_started(
        self, stream: str, key: SessionKey
    ) -> None:
        """Write ``SessionStarted`` on first touch of the main stream only.

        Per SCHEMA_v2 §3.5, subagent streams don't carry their own
        ``SessionStarted`` — that role belongs to ``SubagentStarted`` on the
        parent stream. This adapter doesn't emit ``SubagentStarted`` (the SDK
        doesn't expose the lifecycle signals), so subagent streams start
        straight into ``ClaudeSDKEntry``.
        """
        if stream in self._started_streams:
            return
        self._started_streams.add(stream)

        if key.get("subpath"):
            # Subagent stream — no SessionStarted per v2.
            return

        started = SessionStarted(
            app_name=self._app_name,
            user_id=self._user_id,
            timestamp=datetime.now(UTC),
            extensions={
                CLAUDE_SDK_EXTENSION_KEY: {
                    "project_key": key.get("project_key"),
                    "session_id": key.get("session_id"),
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
