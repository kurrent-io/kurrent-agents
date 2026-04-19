"""Agent with a ``remember`` tool for persisting user facts + ADK's
``load_memory_tool`` for recall.

Combines:

- A custom ``remember(fact)`` tool that calls ``tool_context.add_memory(...)``
  → writes a canonical ``FactRetained`` event to the
  ``AgentMemory-{app}-{user}`` stream via the Kurrent-backed memory service.
- ADK's built-in ``load_memory_tool`` which exposes ``load_memory(query)``
  to the LLM and delegates to ``BaseMemoryService.search_memory``.

Same (app_name, user_id) scope across sessions means facts retained in one
session are recalled in every subsequent session for that user.
"""

from __future__ import annotations

from google.adk import Agent
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import load_memory
from google.adk.tools.tool_context import ToolContext
from google.genai import types


async def remember(fact: str, tool_context: ToolContext) -> dict:
    """Retain one concise fact about the user for future conversations.

    Args:
        fact: A single self-contained statement, e.g. "User's name is Alice".

    Returns:
        A dict describing whether the fact was retained.
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
    name="memory_agent",
    model=LiteLlm(model="anthropic/claude-haiku-4-5-20251001"),
    description="Friendly assistant that remembers user facts across sessions.",
    instruction=(
        "You are a friendly assistant with long-term memory.\n\n"
        "WHEN TO WRITE: When the user tells you something about themselves — "
        "their name, role, where they work, preferences, interests, context — "
        "call the `remember` tool once per distinct fact. Each call stores one "
        "concise, self-contained statement like \"User's name is Alexey\" or "
        "\"User prefers dark mode\".\n\n"
        "WHEN TO READ: When the user asks what you know about them, or asks a "
        "question that depends on prior context about them, call `load_memory` "
        "with a natural-language query and use the results in your answer. "
        "Do not invent facts you cannot find in memory.\n\n"
        "Keep responses short and natural."
    ),
    tools=[remember, load_memory],
)
