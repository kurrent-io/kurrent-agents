"""Agent factory — builds fresh ``Agent`` instances wired to a given
``KurrentDBSessionManager``. Analogous to ADK's ``samples/basic_agent/agent.py``.

Defined as a factory (not a module-level constant) because a Strands Agent
ties itself to one ``session_manager`` / ``agent_id`` on construction; the
demo runs two Agent instances sharing a session to exercise cross-Runner
resume.
"""

from __future__ import annotations

from strands import Agent, tool
from strands.models.anthropic import AnthropicModel

from kurrent_strands import KurrentDBSessionManager

MODEL_ID = "claude-haiku-4-5-20251001"


@tool
def get_weather(city: str) -> dict:
    """Look up current weather for a city.

    Args:
        city: The city name.

    Returns:
        A dict with ``temperature_c`` (Celsius) and ``conditions``, or an
        ``error`` key when the city is not in the local database.
    """
    db = {
        "tokyo": {"temperature_c": 22, "conditions": "sunny"},
        "london": {"temperature_c": 14, "conditions": "rainy"},
        "new york": {"temperature_c": 18, "conditions": "cloudy"},
        "sydney": {"temperature_c": 25, "conditions": "clear"},
    }
    entry = db.get(city.lower().strip())
    if entry is None:
        return {
            "status": "not_found",
            "city": city,
            "error": "City not in the local weather database.",
        }
    return {"status": "success", "city": city, **entry}


def build_agent(session_manager: KurrentDBSessionManager, *, agent_id: str) -> Agent:
    """Build a fresh Agent wired to the given session manager."""
    return Agent(
        agent_id=agent_id,
        name="basic_strands_agent",
        model=AnthropicModel(model_id=MODEL_ID, max_tokens=1024),
        system_prompt=(
            "You are a friendly assistant. When a user asks about weather, "
            "use the get_weather tool. If a user refers back to a previous "
            "answer, rely on the conversation history — do not call the tool "
            "again. Keep responses short."
        ),
        tools=[get_weather],
        session_manager=session_manager,
    )
