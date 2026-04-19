"""Research-agent definition.

Exercises every KurrentDB-backed service in one place:

- ``save_note`` / ``load_note`` / ``list_notes`` — user-scoped artifacts
  (persist across sessions so a note saved in one session is visible in all
  others for the same user).
- ``remember`` / built-in ``load_memory`` — cross-session memory.

User-scoped artifact access is done by calling the service directly via
``tool_context._invocation_context.artifact_service`` because ADK's
ToolContext convenience wrappers always pass ``session_id=session.id``. This
is the only way today to reach the session-less artifact scope from within a
tool. If you squint at ``_invocation_context``: yes, it's private. Documented
here as a sample pattern, not a library API.
"""

from __future__ import annotations

from google.adk import Agent
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import load_memory
from google.adk.tools.tool_context import ToolContext
from google.genai import types


def _services(tool_context: ToolContext):
    """Shorthand accessor for the scoped services + identifiers."""
    inv = tool_context._invocation_context  # noqa: SLF001
    return (
        inv.artifact_service,
        inv.memory_service,
        inv.app_name,
        inv.user_id,
    )


async def save_note(
    name: str, content: str, tool_context: ToolContext
) -> dict:
    """Save a note under ``name``. Notes persist across all of the user's sessions.

    Saving a note with an existing name creates a new version rather than
    overwriting. Use ``load_note(version=N)`` to read an older version.

    Args:
        name: Short identifier for the note, e.g. ``event-sourcing``.
        content: Free-form text to store.
    """
    if not name or not content:
        return {"status": "skipped", "reason": "name and content are required"}
    artifact_service, _, app_name, user_id = _services(tool_context)
    version = await artifact_service.save_artifact(
        app_name=app_name,
        user_id=user_id,
        # session_id omitted → user-scoped, survives session boundaries.
        filename=name,
        artifact=types.Part(text=content),
    )
    return {"status": "saved", "name": name, "version": version}


async def load_note(
    name: str, tool_context: ToolContext, version: int | None = None
) -> dict:
    """Load a previously-saved note by name.

    Args:
        name: The note's identifier (as passed to ``save_note``).
        version: Optional specific version; latest when omitted.
    """
    artifact_service, _, app_name, user_id = _services(tool_context)
    part = await artifact_service.load_artifact(
        app_name=app_name, user_id=user_id, filename=name, version=version
    )
    if part is None:
        return {"status": "not_found", "name": name}
    return {
        "status": "loaded",
        "name": name,
        "content": part.text or "",
        "version": version,
    }


async def list_notes(tool_context: ToolContext) -> dict:
    """List every note the user has saved across all sessions."""
    artifact_service, _, app_name, user_id = _services(tool_context)
    keys = await artifact_service.list_artifact_keys(
        app_name=app_name, user_id=user_id
    )
    return {"notes": keys}


async def remember(fact: str, tool_context: ToolContext) -> dict:
    """Retain one concise fact about the user for future conversations.

    Args:
        fact: A single self-contained statement, e.g. "User is researching
              event sourcing".
    """
    if not fact or not fact.strip():
        return {"status": "skipped", "reason": "empty fact"}
    await tool_context.add_memory(
        memories=[
            MemoryEntry(
                content=types.Content(role="user", parts=[types.Part(text=fact)]),
                author="agent",
            )
        ],
    )
    return {"status": "retained", "fact": fact}


root_agent = Agent(
    name="research_agent",
    model=LiteLlm(model="anthropic/claude-haiku-4-5-20251001"),
    description=(
        "Research assistant that keeps notes (as artifacts) and remembers "
        "topics (in memory) across sessions."
    ),
    instruction=(
        "You are a research assistant.\n\n"
        "WHEN THE USER SHARES SOMETHING WORTH KEEPING:\n"
        "  - Call `save_note` with a short kebab-case name and the full content.\n"
        "  - Call `remember` to persist a one-line summary of the topic they are "
        "working on (e.g. \"User is researching event sourcing\").\n"
        "  - Saving a note with an existing name creates a new version; mention "
        "the version you saved.\n\n"
        "WHEN THE USER ASKS WHAT THEY HAVE:\n"
        "  - Call `list_notes` to enumerate saved notes.\n"
        "  - Call `load_memory` with a natural query to recall what they have "
        "been researching.\n\n"
        "WHEN THE USER ASKS TO SEE A NOTE:\n"
        "  - Call `load_note` with the note name; read back the content in your "
        "response.\n\n"
        "Keep responses short and direct. Never invent note contents or facts "
        "you cannot retrieve."
    ),
    tools=[save_note, load_note, list_notes, remember, load_memory],
)
