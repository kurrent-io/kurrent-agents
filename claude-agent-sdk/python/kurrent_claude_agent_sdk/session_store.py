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
from typing import TYPE_CHECKING, Any

from kurrent_agent_schema import SessionStarted
from kurrent_agent_schema.streams import agent_session_stream, agent_subsession_stream
from kurrentdbclient import AsyncKurrentDBClient, StreamState
from kurrentdbclient.exceptions import NotFoundError

from . import _serialization
from .events import CLAUDE_SDK_EXTENSION_KEY, ClaudeSDKEntry

if TYPE_CHECKING:  # pragma: no cover
    from claude_agent_sdk import SessionKey, SessionStoreEntry

logger = logging.getLogger("kurrent_claude_agent_sdk.session_store")


_SUBAGENT_PATH_PREFIX = "subagents/"


def _subagent_id_from_subpath(subpath: str) -> str:
    """Extract an ``agent_id`` from the SDK's ``SessionKey.subpath``.

    The SDK documents ``subpath`` as an opaque ``/``-separated string; the
    observed shape is ``subagents/agent-{id}``. We strip whitespace and outer
    slashes, drop the documented ``subagents/`` prefix when present, and
    collapse any remaining path separators into ``_`` so the resulting stream
    name (``AgentSubsession-{session}-{agent_id}``) stays a single segment
    *and* a multi-segment subpath like ``subagents/team/worker-1`` maps to a
    distinct ``agent_id`` (``team_worker-1``) rather than colliding with a
    differently-prefixed subpath on its final segment alone. Empty input
    (after normalisation) is rejected so trailing-slash / malformed subpaths
    don't silently route into an ``AgentSubsession-{session}-`` collision.
    """
    # Strip only leading whitespace + leading slashes here so trailing-slash
    # shapes like "subagents/" fall through the prefix strip into an empty
    # agent_id and trip the final check (rather than silently collapsing to
    # the bare prefix string).
    normalized = subpath.strip().lstrip("/")
    if normalized.startswith(_SUBAGENT_PATH_PREFIX):
        normalized = normalized[len(_SUBAGENT_PATH_PREFIX):]
    agent_id = normalized.replace("/", "_")
    if not agent_id:
        raise ValueError(
            f"SessionKey.subpath={subpath!r} yielded an empty agent_id; "
            "expected e.g. 'subagents/agent-{id}'."
        )
    return agent_id


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
            now = datetime.now(UTC)

            to_append: list[Any] = []
            # For the main transcript, prepend one ``SessionStarted`` on first
            # touch (in this process). Bundled with the entries batch so the
            # whole thing goes in a single ``StreamState.ANY`` append — matches
            # the MAF Python pattern (``KurrentDBHistoryProvider.save_messages``).
            # Per SCHEMA_v2 §3.5 subagent streams don't carry their own
            # ``SessionStarted``; that role belongs to ``SubagentStarted`` on
            # the parent stream (not emitted by this adapter today).
            if stream not in self._started_streams and not subpath:
                to_append.append(
                    _serialization.serialize(
                        SessionStarted(
                            app_name=self._app_name,
                            user_id=self._user_id,
                            timestamp=now,
                            extensions={
                                CLAUDE_SDK_EXTENSION_KEY: {
                                    "project_key": key.get("project_key"),
                                    "session_id": key.get("session_id"),
                                }
                            },
                        )
                    )
                )
                self._started_streams.add(stream)

            for entry in entries:
                to_append.append(
                    _serialization.serialize(
                        ClaudeSDKEntry(
                            entry_type=str(entry.get("type") or ""),
                            entry_uuid=str(entry.get("uuid") or ""),
                            entry_timestamp=str(entry.get("timestamp") or ""),
                            raw_entry=dict(entry),
                            subpath=subpath,
                            timestamp=now,
                            extensions={
                                CLAUDE_SDK_EXTENSION_KEY: {
                                    "project_key": key.get("project_key"),
                                }
                            },
                        )
                    )
                )

            await self._client.append_to_stream(
                stream,
                events=to_append,
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
        as "no such session", prompting a fresh subprocess spawn. Any
        unparseable individual events in the stream are skipped (with a
        warning from :func:`_serialization.deserialize`) so one corrupt
        record can't crash ``--resume``.
        """
        try:
            stream = self._stream_for(key)
        except ValueError:
            # Malformed subpath: there is nothing to load. Log + return None so
            # the SDK spawns a fresh subprocess rather than propagating.
            logger.warning(
                "KurrentDBSessionStore.load: cannot derive stream name from "
                "SessionKey=%r — treating as no-such-session.",
                key,
            )
            return None

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

