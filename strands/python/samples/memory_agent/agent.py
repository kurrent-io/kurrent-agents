"""Memory-agent factory — Strands ``Agent`` wired to ``KurrentDBSessionManager``
and ``KurrentDBAgentMemory``. Exposes a ``remember`` tool and a
``recall_memory`` tool, both closed over a single memory instance.

Strands doesn't ship a built-in memory-service abstraction like ADK's
``LoadMemoryTool``, so we provide both write and read sides as explicit tools.
Memory is scoped by (``app_name``, ``user_id``), so facts retained in one
session are visible in every other session for the same user — including
across framework boundaries (ADK agents can read the same stream).
"""

from __future__ import annotations

from strands import Agent, tool
from strands.models.anthropic import AnthropicModel

from kurrent_strands import KurrentDBAgentMemory, KurrentDBSessionManager

MODEL_ID = "claude-haiku-4-5-20251001"


def _build_tools(memory: KurrentDBAgentMemory) -> list:
    """Return ``remember`` + ``recall_memory`` tools bound to ``memory``."""

    @tool
    def remember(fact: str) -> dict:
        """Retain a single self-contained fact about the user.

        Use one call per distinct fact. Example facts:
          - "User's name is Alice"
          - "User prefers dark mode"

        Args:
            fact: A short, self-contained statement about the user.
        """
        if not fact or not fact.strip():
            return {"status": "skipped", "reason": "empty fact"}
        memory.retain(fact)
        return {"status": "retained", "fact": fact}

    @tool
    def recall_memory(query: str) -> dict:
        """Fetch everything previously retained about the user.

        The v1 implementation ignores ``query`` and returns every fact;
        treat it as a search hint only.

        Args:
            query: Natural-language description of what you're looking for.
        """
        facts = memory.recall(query)
        return {"facts": facts}

    return [remember, recall_memory]


def build_agent(
    *,
    session_manager: KurrentDBSessionManager,
    memory: KurrentDBAgentMemory,
    agent_id: str,
) -> Agent:
    """Build an Agent wired to the given SessionManager + memory."""
    return Agent(
        agent_id=agent_id,
        name="memory_strands_agent",
        model=AnthropicModel(model_id=MODEL_ID, max_tokens=1024),
        system_prompt=(
            "You are a friendly assistant with long-term memory about the "
            "user.\n\n"
            "WRITE: When the user tells you something about themselves — "
            "their name, role, where they work, preferences — call `remember` "
            "once per distinct fact. Keep each fact short and self-contained.\n"
            "READ: When the user asks what you know about them, or needs a "
            "previous detail, call `recall_memory` with a natural-language "
            "query and use the returned facts. Do not invent facts not in the "
            "result.\n"
            "Keep responses short."
        ),
        tools=_build_tools(memory),
        session_manager=session_manager,
    )
