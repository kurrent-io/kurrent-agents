"""Canonical stream-name builders.

See ``schema/SCHEMA_v2.md §2`` for the full stream taxonomy.
"""

from __future__ import annotations

AGENT_SESSION_PREFIX: str = "AgentSession-"
AGENT_SUBSESSION_PREFIX: str = "AgentSubsession-"
AGENT_MEMORY_PREFIX: str = "AgentMemory-"
AGENT_ARTIFACT_PREFIX: str = "AgentArtifact-"
EVAL_RUN_PREFIX: str = "EvalRun-"


def agent_session_stream(session_id: str) -> str:
    """Primary conversation stream for a session."""
    return f"{AGENT_SESSION_PREFIX}{session_id}"


def agent_subsession_stream(parent_session_id: str, agent_id: str) -> str:
    """Subagent conversation stream, scoped under a parent session.

    See ``schema/SCHEMA_v2.md §3.5``.
    """
    return f"{AGENT_SUBSESSION_PREFIX}{parent_session_id}-{agent_id}"


def agent_memory_stream(app_name: str, user_id: str) -> str:
    """Per-app, per-user retained facts. v1 default scope.

    See ``schema/SCHEMA_v2.md §3.7`` (inherited from v1 §3.6).
    """
    return f"{AGENT_MEMORY_PREFIX}{app_name}-{user_id}"


def agent_artifact_stream(scope: str, filename: str) -> str:
    """Binary artifact versions. ``scope`` is integration-defined."""
    return f"{AGENT_ARTIFACT_PREFIX}{scope}-{filename}"


def eval_run_stream(run_id: str) -> str:
    """Eval run events."""
    return f"{EVAL_RUN_PREFIX}{run_id}"
