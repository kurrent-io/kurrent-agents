"""Stream naming conventions. Must match the C# implementation."""


def for_session(session_id: str) -> str:
    """Stream for a single agent session's events.

    Category: ``AgentSession`` — enables ``$ce-AgentSession`` for cross-session queries.
    """
    return f"AgentSession-{session_id}"
