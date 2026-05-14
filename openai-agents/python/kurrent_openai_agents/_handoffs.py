"""Per-session handoff state + pure routing logic.

Hosts the data structures populated by ``KurrentDBSession`` 's RunHooksBase
overrides (``on_handoff``, ``on_agent_start``, ``on_agent_end``) and consumed
by ``add_items`` to split a flat OpenAI Agents item list into per-stream
segments matching SCHEMA_v2 §3.5 subagent lifecycle.

No I/O here. ``session.py`` owns all KurrentDB interactions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from google.protobuf.message import Message as ProtoMessage

logger = logging.getLogger(__name__)

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
class PendingHandoffCall:
    """A handoff function_call observed in an LLM response, awaiting on_handoff.

    Populated by ``on_llm_end`` for every function call in the model's output
    whose ``name`` matches one of the source agent's registered handoff tool
    names. The SDK may emit multiple handoff calls per response and pick one;
    the chosen one is resolved by ``on_handoff`` matching on (from_name,
    target_name_hint) and the unchosen ones are dropped on the next
    ``on_llm_end`` (the list is rebuilt per LLM response).
    """

    from_name: str
    target_name_hint: str
    tool_name: str
    call_id: str


@dataclass(slots=True)
class ExpectedHandoff:
    """Stashed by ``on_handoff`` until the matching ``function_call`` arrives.

    ``tool_name`` and ``call_id`` come from a ``PendingHandoffCall`` captured
    in ``on_llm_end``. ``route_items`` requires both to match exactly,
    eliminating the ambiguity of name-only matching when the model emits
    several function calls in one response. When the on_llm_end path didn't
    populate a pending call (e.g. tests that build ExpectedHandoff directly),
    ``call_id`` may be ``None``: ``route_items`` then falls back to name-only
    matching (with the ``transfer_to_`` prefix heuristic when ``tool_name``
    is also ``None``).
    """

    from_name: str
    to_name: str
    to_agent: Any  # agents.Agent — kept as Any to avoid an import cycle
    tool_name: str | None = None
    call_id: str | None = None


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
    # Handoff function calls observed in the most recent on_llm_end. Rebuilt
    # per LLM response; on_handoff pops the chosen entry to resolve
    # (tool_name, call_id) for the pending ExpectedHandoff.
    pending_calls: list[PendingHandoffCall] = field(default_factory=list)
    # call_id → summary: populated by on_agent_end, drained at the end of
    # route_items so SubagentCompleted lands AFTER the target's final items.
    pending_close: dict[str, str] = field(default_factory=dict)
    current_owner: str = ""  # set in __post_init__

    def __post_init__(self) -> None:
        if not self.current_owner:
            self.current_owner = self.parent_stream

    def pop_pending_call(
        self, *, from_name: str, to_name: str
    ) -> PendingHandoffCall | None:
        """Remove and return the first matching pending handoff call.

        Match is on (from_name, target_name_hint=to_name) — the
        ``from_agent`` that fired the handoff and the target's name. The SDK
        picks one handoff out of any candidates the model emitted, and that
        one is what on_handoff is called with, so this single-shot pop is
        the right shape.
        """
        for i, entry in enumerate(self.pending_calls):
            if entry.from_name == from_name and entry.target_name_hint == to_name:
                return self.pending_calls.pop(i)
        return None


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
            expected = ledger.expected
            func_name = item.get("name") or ""
            func_call_id = item.get("call_id") or ""
            # Strictest match first: when on_llm_end captured the exact
            # (tool_name, call_id) for this handoff, both must match. This is
            # robust against parallel tool calls in the same response.
            # Otherwise fall back to name-only matching, finally the
            # transfer_to_ prefix heuristic when no Handoff config was
            # resolvable at all.
            if expected.call_id is not None:
                exact_match = (
                    func_call_id == expected.call_id
                    and func_name == expected.tool_name
                )
                name_match = exact_match
            elif expected.tool_name:
                name_match = func_name == expected.tool_name
            else:
                name_match = func_name.startswith("transfer_to_")
            if not name_match:
                # An unrelated tool call slipped in before the actual handoff
                # call. Persist it as a canonical tool call on current_owner;
                # keep `expected` pending for the real handoff_call to follow.
                canonical_events = items_to_canonical([item], start_index=message_index, timestamp=timestamp)
                queue_single(ledger.current_owner, canonical_events)
                continue
            call_id = func_call_id
            if not call_id:
                # Malformed handoff_call (no call_id) — fall through to the
                # normal codec rather than crashing. Keep the expected entry
                # pending so a later well-formed call_id can still promote.
                logger.warning(
                    "function_call missing call_id while handoff %r→%r is expected; "
                    "falling through to canonical tool call (no SubagentStarted emitted).",
                    expected.from_name, expected.to_name,
                )
                canonical_events = items_to_canonical([item], start_index=message_index, timestamp=timestamp)
                queue_single(ledger.current_owner, canonical_events)
                continue
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

        canonical_events = items_to_canonical([item], start_index=message_index, timestamp=timestamp)
        queue_single(ledger.current_owner, canonical_events)

    flush_pending()

    # Drain pending_close — emit SubagentCompleted for any subagent whose
    # on_agent_end fired before this add_items call. The close lands AFTER
    # the target's final-batch items so the subsession transcript is
    # complete before the lifecycle marker.
    for call_id, summary in list(ledger.pending_close.items()):
        active = ledger.active.pop(call_id, None)
        if active is None:
            del ledger.pending_close[call_id]
            continue
        evt = _emit_subagent_completed_synthetic(
            agent_id=active.agent_id, summary=summary,
            message_index=start_index + len(items), timestamp=timestamp,
        )
        ops.append(DualAppend(streams=(ledger.parent_stream, active.subsession_stream), event=evt))
        del ledger.pending_close[call_id]
        ledger.current_owner = ledger.parent_stream

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


def _emit_subagent_completed_synthetic(
    *,
    agent_id: str,
    summary: str,
    message_index: int,
    timestamp: datetime,
) -> ProtoMessage:
    """Build a SubagentCompleted lifecycle marker WITHOUT an underlying SDK item.

    The close is triggered by ``on_agent_end``, not by a ``function_call_output``
    dict, so there is no ``raw_item`` to store. Cross-framework readers should skip
    SubagentCompleted (it has no flat-list correlate); the subsession stream's
    ToolResultReceived for the handoff_output serves as the SDK-visible "transfer"
    record.
    """
    from kurrent_agent_schema import SubagentCompleted

    evt = SubagentCompleted(agent_id=agent_id, outcome="success")
    if summary:
        evt.summary = summary[:512]
    evt.timestamp.FromDatetime(timestamp.replace(tzinfo=None))
    del message_index  # SubagentCompleted has no message_index field
    return evt
