"""Per-session revision tracking for optimistic concurrency control.

See ``DESIGN.md`` §8 for the full concurrency contract. Summary: each
``KurrentDBSessionService`` instance keeps a map from ``(app, user, session_id)``
to the last-known revision on each stream it writes. Appends pass the
expected-revision; on ``WrongExpectedVersion`` the service reads the catch-up
range, folds new state deltas into the in-memory ``Session``, and retries once
before raising ``StaleSessionError``.

This module owns the map and exposes a small type-safe API; actual append-retry
logic lives in ``session_service.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


class StaleSessionError(Exception):
    """Raised when optimistic concurrency repeatedly fails for one session.

    Mirrors ADK's stale-session contract (see ``sessions/session.py``'s
    ``_storage_update_marker``). The caller should drop the in-memory
    ``Session`` and re-read via ``get_session`` before any further writes.
    """


@dataclass(frozen=True)
class SessionKey:
    """Composite key for the per-session revision map."""

    app_name: str
    user_id: str
    session_id: str


@dataclass
class _StreamRevisions:
    """Last-known revisions for the three streams that one session writes to.

    ``None`` means the stream has never been written by this service instance.
    """

    session: int | None = None
    app_state: int | None = None
    user_state: int | None = None


class RevisionTracker:
    """Thread-safe map from ``SessionKey`` to last-known stream revisions.

    Single-process; not persisted. A new process starts with an empty tracker
    and learns revisions through normal reads and writes.
    """

    def __init__(self) -> None:
        self._revisions: dict[SessionKey, _StreamRevisions] = {}
        self._lock = Lock()

    def get(self, key: SessionKey) -> _StreamRevisions:
        with self._lock:
            return self._revisions.setdefault(key, _StreamRevisions())

    def record_session(self, key: SessionKey, revision: int) -> None:
        with self._lock:
            entry = self._revisions.setdefault(key, _StreamRevisions())
            entry.session = revision

    def record_app_state(self, key: SessionKey, revision: int) -> None:
        with self._lock:
            entry = self._revisions.setdefault(key, _StreamRevisions())
            entry.app_state = revision

    def record_user_state(self, key: SessionKey, revision: int) -> None:
        with self._lock:
            entry = self._revisions.setdefault(key, _StreamRevisions())
            entry.user_state = revision

    def forget(self, key: SessionKey) -> None:
        with self._lock:
            self._revisions.pop(key, None)
