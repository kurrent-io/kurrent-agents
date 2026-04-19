"""Agent definition — importable by both `adk run` and the demo runner.

Matches the ADK agent-directory convention so that `adk run samples/basic_agent`
could drive this agent directly if desired (though the full KurrentDB wiring
happens in ``main.py``).
"""

from __future__ import annotations

from google.adk import Agent
from google.adk.models.lite_llm import LiteLlm


def get_weather(city: str) -> dict:
    """Look up current weather for a city.

    Args:
        city: The city name.

    Returns:
        A dict with temperature (Celsius) and conditions, or an error.
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


root_agent = Agent(
    name="basic_agent",
    model=LiteLlm(model="anthropic/claude-haiku-4-5"),
    description="Helpful assistant that can look up the weather for a handful of cities.",
    instruction=(
        "You are a friendly assistant. When a user asks about weather, use the "
        "get_weather tool. If a user refers back to a previous answer, rely on "
        "the conversation history — do not call the tool again."
    ),
    tools=[get_weather],
)
