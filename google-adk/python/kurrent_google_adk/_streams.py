"""Stream-name builders.

Function names are unchanged from v1 (``for_session``, ``for_app_state``,
``for_user_state``, ``for_credentials``, ``for_memory``, ``for_artifact``,
``for_eval_run``). Output stream prefixes follow ``schema/SCHEMA_v2.md``:

* ADK-owned framework-specific streams drop the ``Agent-`` prefix
  (``AppState-`` / ``UserState-`` / ``Credentials-``) per §2.2.
* Canonical shared-stream names (``AgentSession-``, ``AgentMemory-``,
  ``AgentArtifact-``, ``EvalRun-``) come from the shared package.

ADK-specific id normalisation lives here because the shared builders are
deliberately permissive (Claude SDK uses free-form session ids); ADK requires
``str.isidentifier()``-style ``app_name`` checks and URL-encoding for
``user_id`` / ``filename`` / ``session_id``.
"""

from __future__ import annotations

import re
import urllib.parse

from kurrent_agent_schema.streams import (
    agent_artifact_stream,
    agent_memory_stream,
    agent_session_stream,
    eval_run_stream,
)

# Max length per id segment after normalisation, to keep stream names manageable.
_MAX_SEGMENT_LENGTH = 128

# Characters that pass through unmodified when normalising free-form ids.
_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"

_APP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalise_id(value: str, *, field: str) -> str:
    """Percent-encode any characters outside the safe set and bound the length."""
    if not value:
        raise ValueError(f"{field} cannot be empty")
    encoded = urllib.parse.quote(value, safe=_SAFE_ID_CHARS)
    if len(encoded) > _MAX_SEGMENT_LENGTH:
        raise ValueError(
            f"{field} exceeds {_MAX_SEGMENT_LENGTH}-char segment budget after normalisation "
            f"(got {len(encoded)}): {value!r}"
        )
    return encoded


def _validate_app_name(app_name: str) -> str:
    """ADK's ``App.name`` must satisfy ``str.isidentifier()`` and not equal ``"user"``."""
    if not _APP_NAME_RE.match(app_name):
        raise ValueError(
            f"app_name must be a valid Python identifier (got {app_name!r}); "
            "ADK enforces this in apps/app.py:30."
        )
    if app_name == "user":
        raise ValueError('app_name cannot be "user" (reserved in ADK).')
    return app_name


# ---- Shared canonical streams ----------------------------------------------


def for_session(session_id: str) -> str:
    """Shared session stream. Category prefix: ``AgentSession``."""
    return agent_session_stream(_normalise_id(session_id, field="session_id"))


def for_memory(app_name: str, user_id: str) -> str:
    """Canonical per-app, per-user memory stream (SCHEMA_v2.md §2.1)."""
    return agent_memory_stream(
        _validate_app_name(app_name),
        _normalise_id(user_id, field="user_id"),
    )


def for_artifact(
    *,
    app_name: str,
    user_id: str,
    filename: str,
    session_id: str | None = None,
) -> str:
    """Per-artifact stream. Session-scoped unless ``session_id`` is ``None``."""
    app = _validate_app_name(app_name)
    user = _normalise_id(user_id, field="user_id")
    file = _normalise_id(filename, field="filename")
    if session_id is None:
        return agent_artifact_stream(f"{app}-{user}", file)
    session = _normalise_id(session_id, field="session_id")
    return agent_artifact_stream(f"{app}-{user}-{session}", file)


def for_eval_run(run_id: str) -> str:
    """Evaluation results stream (SCHEMA_v2.md §3.7)."""
    return eval_run_stream(_normalise_id(run_id, field="run_id"))


# ---- ADK-owned framework-specific streams (SCHEMA_v2.md §2.2) ---------------


def for_app_state(app_name: str) -> str:
    """ADK app-scoped state (``app:`` prefix keys)."""
    return f"AppState-{_validate_app_name(app_name)}"


def for_user_state(app_name: str, user_id: str) -> str:
    """ADK user-scoped state (``user:`` prefix keys)."""
    return (
        f"UserState-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
    )


def for_credentials(app_name: str, user_id: str) -> str:
    """ADK-owned tool OAuth credentials stream."""
    return (
        f"Credentials-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
    )


# System category streams — useful for catch-up subscriptions across all sessions.
CATEGORY_SESSION = "$ce-AgentSession"
CATEGORY_MEMORY = "$ce-AgentMemory"
CATEGORY_ARTIFACT = "$ce-AgentArtifact"
CATEGORY_EVAL_RUN = "$ce-EvalRun"


__all__ = [
    "CATEGORY_ARTIFACT",
    "CATEGORY_EVAL_RUN",
    "CATEGORY_MEMORY",
    "CATEGORY_SESSION",
    "for_app_state",
    "for_artifact",
    "for_credentials",
    "for_eval_run",
    "for_memory",
    "for_session",
    "for_user_state",
]
