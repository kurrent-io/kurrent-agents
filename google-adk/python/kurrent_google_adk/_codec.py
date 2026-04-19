"""ADK ``Event`` ↔ canonical event decomposition and reconstruction.

See ``DESIGN.md`` §5 for the full mapping and rationale. Summary:

- **Write path (``event_to_canonical``)**: decompose one ADK ``Event`` into
  one or more canonical events (``SCHEMA.md §5.2``), routing state deltas to
  the correct stream by prefix (``app:`` / ``user:`` / unprefixed). Preserve
  non-canonical fields verbatim in ``extensions.adk``.
- **Read path (``canonical_to_events``)**: group canonical events that share
  the same ``extensions.adk.invocation_id`` and author when they originated
  from a single decomposition, and re-materialise the ADK ``Event``.

Implementation is deferred. The code below defines the API shape; the
concrete transformation lives in a later change once the ADK fixtures and
test harness are in place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — ADK is a dev-time import only.
    from google.adk.events.event import Event as AdkEvent

    from ._schema.events import _EventBase as CanonicalEvent


def event_to_canonical(event: AdkEvent) -> list[CanonicalEvent]:
    """Decompose one ADK ``Event`` into zero or more canonical events.

    Returns events in the order they should be appended to the session stream.
    State-delta routing (to ``AgentAppState-*`` / ``AgentUserState-*`` streams)
    is the caller's responsibility — see ``session_service.append_event``.

    Raises:
        NotImplementedError: until the codec is implemented.
    """
    raise NotImplementedError("codec.event_to_canonical is not implemented yet")


def canonical_to_events(events: list[CanonicalEvent]) -> list[AdkEvent]:
    """Reconstruct ADK ``Event`` objects from an ordered stream of canonical events.

    Canonical events sharing an ``extensions.adk.invocation_id`` and author that
    originated from a single decomposition are merged back into one ADK
    ``Event``. All fields preserved in ``extensions.adk`` are restored.

    Raises:
        NotImplementedError: until the codec is implemented.
    """
    raise NotImplementedError("codec.canonical_to_events is not implemented yet")


def extract_usage_metadata(event: AdkEvent) -> dict[str, Any] | None:
    """Build the ``$usage`` KurrentDB event-metadata payload from an ADK event.

    Returns ``None`` when the event has no ``usage_metadata``. The canonical
    shape is documented in ``SCHEMA.md §3.4``.

    Raises:
        NotImplementedError: until the codec is implemented.
    """
    raise NotImplementedError("codec.extract_usage_metadata is not implemented yet")
