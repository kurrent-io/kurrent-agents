"""Per-session handoff state + pure routing logic.

Hosts the data structures populated by ``KurrentDBSession`` 's RunHooksBase
overrides (``on_handoff``, ``on_agent_start``, ``on_agent_end``) and consumed
by ``add_items`` to split a flat OpenAI Agents item list into per-stream
segments matching SCHEMA_v2 §3.5 subagent lifecycle.

No I/O here. ``session.py`` owns all KurrentDB interactions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from google.protobuf.message import Message as ProtoMessage

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9_]+")
_AGENT_ID_TAIL_LEN = 6


def slug(value: str) -> str:
    """Lowercase + replace any run of non-`[a-z0-9_]` with a single `-`; strip ends.

    Underscore is preserved because SCHEMA_v2 §2.4's stream-name char set
    is `[A-Za-z0-9._-]+` (underscore included).
    """
    return _SLUG_NON_ALNUM.sub("-", value.lower()).strip("-")


def derive_agent_id(target_name: str, call_id: str) -> str:
    """Build a SCHEMA_v2 §2.4-conformant ``agent_id`` for an OpenAI handoff.

    Shape: ``sub-{slug(target_name)}-{call_id[-6:]}``. Unique per handoff
    invocation in a session because ``call_id`` is unique per SDK tool call.
    """
    if not target_name:
        raise ValueError("target_name cannot be empty")
    if not call_id:
        raise ValueError("call_id cannot be empty")
    name_slug = slug(target_name) or "agent"
    tail = call_id[-_AGENT_ID_TAIL_LEN:].lower()
    # Final §2.4 char-set sweep on the tail.
    tail = _SLUG_NON_ALNUM.sub("-", tail).strip("-") or "x"
    return f"sub-{name_slug}-{tail}"


# ----- ledger data ----------------------------------------------------------


@dataclass(slots=True)
class ExpectedHandoff:
    """Stashed by ``on_handoff`` until the next matching ``function_call`` arrives."""

    from_name: str
    to_name: str
    to_agent: Any  # agents.Agent — kept as Any to avoid an import cycle


@dataclass(slots=True)
class ActiveHandoff:
    """Tracks an in-flight subagent invocation until its ``handoff_output`` lands."""

    call_id: str
    agent_id: str
    agent_type: str
    subsession_stream: str


@dataclass(slots=True)
class _HandoffLedger:
    """Per-session mutable state. Lives on a ``KurrentDBSession`` instance.

    Not persisted; rebuilt on process restart via fresh ``on_handoff`` events.
    See spec §5 "Session resume after process restart" for the rationale.
    """

    parent_stream: str
    expected: ExpectedHandoff | None = None
    active: dict[str, ActiveHandoff] = field(default_factory=dict)
    current_owner: str = ""  # set in __post_init__

    def __post_init__(self) -> None:
        if not self.current_owner:
            self.current_owner = self.parent_stream


# ----- write-op types -------------------------------------------------------


@dataclass(slots=True)
class SingleAppend:
    """Append a batch of events to a single stream."""

    stream: str
    events: list[Any]  # list[ProtoMessage | OpenAIItem]


@dataclass(slots=True)
class DualAppend:
    """Atomic dual-stream append of ONE event to TWO streams.

    Used exclusively for ``SubagentStarted`` / ``SubagentCompleted`` per
    SCHEMA_v2 §3.5. The session.add_items writer dispatches this via
    ``multi_append_to_stream`` with two ``NewEvents`` entries.
    """

    streams: tuple[str, str]
    event: ProtoMessage


WriteOp = SingleAppend | DualAppend


# ----- route_items ----------------------------------------------------------


def route_items(
    items: list[dict[str, Any]],
    *,
    ledger: _HandoffLedger,
    session_id: str,
    start_index: int,
    timestamp: datetime,
) -> list[WriteOp]:
    """Walk a flat dict list, emitting an ordered list of write operations.

    Mutates ``ledger`` in place: consumes ``expected``, populates / clears
    ``active``, flips ``current_owner`` on handoff start / end. The returned
    list preserves the write order — the caller MUST honour it so subagent
    transcript items land on the subsession stream **after** the
    ``SubagentStarted`` lifecycle marker.

    Handoff promotion happens **only** when ``ledger.expected`` matches the
    next ``function_call`` (start guard) or ``ledger.active`` matches a
    ``function_call_output``'s ``call_id`` (completion guard). Without those,
    handoff-shaped items fall through to the normal codec (becoming
    ``OpenAIItem`` for the framework-specific fallback) — see spec
    "Hooks not wired" degradation.
    """
    from ._codec import items_to_canonical

    ops: list[WriteOp] = []
    pending: list[Any] = []
    pending_stream: str | None = None

    def flush_pending() -> None:
        nonlocal pending, pending_stream
        if pending and pending_stream is not None:
            ops.append(SingleAppend(stream=pending_stream, events=list(pending)))
        pending.clear()
        pending_stream = None

    def queue_single(stream: str, events: list[Any]) -> None:
        nonlocal pending_stream
        if not events:
            return
        if pending_stream is None:
            pending_stream = stream
            pending.extend(events)
        elif pending_stream == stream:
            pending.extend(events)
        else:
            flush_pending()
            pending_stream = stream
            pending.extend(events)

    for offset, item in enumerate(items):
        message_index = start_index + offset
        kind = item.get("type")

        if kind == "function_call" and ledger.expected is not None:
            flush_pending()
            evt = _emit_subagent_started(item, ledger, session_id, message_index, timestamp)
            # NOTE: ledger.current_owner has just been flipped to the
            # new subsession by _emit_subagent_started — that's the
            # second stream we want on the DualAppend.
            ops.append(DualAppend(
                streams=(ledger.parent_stream, ledger.current_owner),
                event=evt,
            ))
            continue

        if kind == "function_call_output":
            call_id = item.get("call_id") or ""
            if call_id in ledger.active:
                subsession = ledger.active[call_id].subsession_stream
                flush_pending()
                evt = _emit_subagent_completed(item, ledger, message_index, timestamp)
                del ledger.active[call_id]
                ledger.current_owner = ledger.parent_stream
                ops.append(DualAppend(streams=(ledger.parent_stream, subsession), event=evt))
                continue

        canonical_events = items_to_canonical([item], start_index=message_index, timestamp=timestamp)
        queue_single(ledger.current_owner, canonical_events)

    flush_pending()
    return ops


def _emit_subagent_started(
    item: dict[str, Any],
    ledger: _HandoffLedger,
    session_id: str,
    message_index: int,
    timestamp: datetime,
) -> ProtoMessage:
    """Build a ``SubagentStarted`` for the matched handoff_call and update the ledger."""
    from ._codec import _map_handoff_call
    from ._stream_names import for_subsession

    expected = ledger.expected
    assert expected is not None
    call_id = item.get("call_id") or ""
    agent_id = derive_agent_id(expected.to_name, call_id)
    subsession_stream = for_subsession(session_id, agent_id)

    evt = _map_handoff_call(
        item=item,
        agent_id=agent_id,
        agent_type=expected.to_name,
        source_agent=expected.from_name,
        subsession_stream=subsession_stream,
        message_index=message_index,
        timestamp=timestamp,
    )

    ledger.active[call_id] = ActiveHandoff(
        call_id=call_id, agent_id=agent_id, agent_type=expected.to_name,
        subsession_stream=subsession_stream,
    )
    ledger.current_owner = subsession_stream
    ledger.expected = None
    return evt


def _emit_subagent_completed(
    item: dict[str, Any],
    ledger: _HandoffLedger,
    message_index: int,
    timestamp: datetime,
) -> ProtoMessage:
    from ._codec import _map_handoff_output

    call_id = item["call_id"]
    active = ledger.active[call_id]
    return _map_handoff_output(
        item=item, agent_id=active.agent_id,
        message_index=message_index, timestamp=timestamp,
    )
