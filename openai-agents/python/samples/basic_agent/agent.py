"""Agent factory — builds fresh ``Agent`` instances.

The OpenAI Agents SDK's ``Agent`` is a declarative object that owns
instructions + tools + model but not session state; session state lives on
``Session`` instances passed to ``Runner.run``. The factory exists so that
two runs in ``main.py`` can share tool / model configuration without
sharing in-memory state.

By default the agent routes to **Anthropic Claude via LiteLLM** so it works
with the monorepo's existing `ANTHROPIC_API_KEY`. Swap the model for
``"gpt-4o-mini"`` (or any other OpenAI model) by setting ``OPENAI_API_KEY``
and changing the `model=` line below to a bare string.
"""

from __future__ import annotations

import os

from agents import Agent, function_tool
from agents.extensions.models.litellm_model import LitellmModel

CLAUDE_MODEL_ID = "anthropic/claude-haiku-4-5-20251001"


@function_tool
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


def build_agent() -> Agent:
    """Build a fresh Agent. No session state — that lives on ``KurrentDBSession``."""
    return Agent(
        name="basic_openai_agent",
        instructions=(
            "You are a friendly assistant. When a user asks about weather, "
            "use the get_weather tool. If a user refers back to a previous "
            "answer, rely on the conversation history — do not call the tool "
            "again. Keep responses short."
        ),
        tools=[get_weather],
        model=LitellmModel(
            model=CLAUDE_MODEL_ID,
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        ),
    )
