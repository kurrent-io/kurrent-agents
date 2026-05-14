"""Stream-name builders for the OpenAI Agents integration.

Wraps :func:`kurrent_agent_schema.agent_session_stream` and
:func:`kurrent_agent_schema.agent_subsession_stream` with id normalisation
so free-form ids cannot break the category prefix. Conformant with
``schema/SCHEMA_v2.md §2.4``.
"""

from __future__ import annotations

import urllib.parse

from kurrent_agent_schema import agent_session_stream, agent_subsession_stream

_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
_MAX_SEGMENT_LENGTH = 128


def _normalise_id(value: str, *, field: str) -> str:
    if not value:
        raise ValueError(f"{field} cannot be empty")
    encoded = urllib.parse.quote(value, safe=_SAFE_ID_CHARS)
    if len(encoded) > _MAX_SEGMENT_LENGTH:
        raise ValueError(
            f"{field} exceeds {_MAX_SEGMENT_LENGTH}-char segment budget after "
            f"normalisation (got {len(encoded)}): {value!r}"
        )
    return encoded


def for_session(session_id: str) -> str:
    """Primary conversation stream — ``AgentSession-{normalised_session_id}``."""
    return agent_session_stream(_normalise_id(session_id, field="session_id"))


def for_subsession(parent_session_id: str, agent_id: str) -> str:
    """Subagent conversation stream — ``AgentSubsession-{parent}-{agent_id}``.

    See ``schema/SCHEMA_v2.md §3.5`` for the canonical shape and §2.4 for
    the identifier rules enforced by the underlying normaliser.
    """
    return agent_subsession_stream(
        _normalise_id(parent_session_id, field="parent_session_id"),
        _normalise_id(agent_id, field="agent_id"),
    )
