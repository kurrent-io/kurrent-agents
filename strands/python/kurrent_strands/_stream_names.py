"""Stream-name builders and id normalisation.

Stream naming follows ``schema/SCHEMA.md`` §2. Category prefixes are fixed
(``AgentSession-``, ``AgentMemory-``, …) so ``$ce-*`` system streams work
across frameworks.
"""

from __future__ import annotations

import re
import urllib.parse

# Max length per id segment after normalisation, to keep stream names manageable.
_MAX_SEGMENT_LENGTH = 128

# Characters that pass through unmodified when normalising free-form ids.
_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"


def _normalise_id(value: str, *, field: str) -> str:
    """Percent-encode any characters outside the safe set and bound the length.

    ``field`` is only used to produce clear error messages.
    """
    if not value:
        raise ValueError(f"{field} cannot be empty")
    encoded = urllib.parse.quote(value, safe=_SAFE_ID_CHARS)
    if len(encoded) > _MAX_SEGMENT_LENGTH:
        raise ValueError(
            f"{field} exceeds {_MAX_SEGMENT_LENGTH}-char segment budget after normalisation "
            f"(got {len(encoded)}): {value!r}"
        )
    return encoded


_APP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_app_name(app_name: str) -> str:
    """ADK's ``App.name`` must satisfy ``str.isidentifier()`` and not equal ``"user"``.

    See ``src/google/adk/apps/app.py:30``. No normalisation needed — ADK
    enforces safe characters already.
    """
    if not _APP_NAME_RE.match(app_name):
        raise ValueError(
            f"app_name must be a valid Python identifier (got {app_name!r}); "
            "ADK enforces this in apps/app.py:30."
        )
    if app_name == "user":
        raise ValueError('app_name cannot be "user" (reserved in ADK).')
    return app_name


def for_session(session_id: str) -> str:
    """Shared session stream. Category prefix: ``AgentSession``."""
    return f"AgentSession-{_normalise_id(session_id, field='session_id')}"


def for_app_state(app_name: str) -> str:
    """ADK app-scoped state (`app:` prefix keys)."""
    return f"AgentAppState-{_validate_app_name(app_name)}"


def for_user_state(app_name: str, user_id: str) -> str:
    """ADK user-scoped state (`user:` prefix keys)."""
    return (
        f"AgentUserState-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
    )


def for_credentials(app_name: str, user_id: str) -> str:
    """ADK-owned tool OAuth credentials stream."""
    return (
        f"AgentCredentials-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
    )


def for_memory(app_name: str, user_id: str) -> str:
    """Canonical per-app, per-user memory stream (SCHEMA.md §3.6)."""
    return (
        f"AgentMemory-{_validate_app_name(app_name)}"
        f"-{_normalise_id(user_id, field='user_id')}"
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
        return f"AgentArtifact-{app}-{user}-{file}"
    session = _normalise_id(session_id, field="session_id")
    return f"AgentArtifact-{app}-{user}-{session}-{file}"


def for_eval_run(run_id: str) -> str:
    """Evaluation results stream (SCHEMA.md §3.5)."""
    return f"EvalRun-{_normalise_id(run_id, field='run_id')}"


# System category streams — useful for catch-up subscriptions across all sessions
# ($ce-AgentSession is maintained by KurrentDB as the system category projection).
CATEGORY_SESSION = "$ce-AgentSession"
CATEGORY_MEMORY = "$ce-AgentMemory"
CATEGORY_ARTIFACT = "$ce-AgentArtifact"
CATEGORY_EVAL_RUN = "$ce-EvalRun"
