"""Shared in-memory weather lookup tool used by every showcase real-mode runner.

Mirrors ``demo/weather_demo/config.py`` but smaller scope — the showcase
demo only needs the function, not the multi-turn TURNS list.
"""

from __future__ import annotations

WEATHER_DB = {
    "tokyo": {"temperature_c": 22, "conditions": "sunny"},
    "london": {"temperature_c": 14, "conditions": "rainy"},
    "new york": {"temperature_c": 18, "conditions": "cloudy"},
    "sydney": {"temperature_c": 25, "conditions": "clear"},
    "oslo": {"temperature_c": 8, "conditions": "light_rain"},
    "paris": {"temperature_c": 16, "conditions": "partly_cloudy"},
}

SYSTEM_PROMPT = (
    "You are a friendly assistant. When the user asks about weather, use the "
    "get_weather tool with the city name. If the user references a previous "
    "turn, use the conversation history rather than calling the tool again. "
    "Keep responses short — one or two sentences."
)

MODEL_ID = "claude-haiku-4-5-20251001"


def lookup_weather(city: str) -> dict:
    """Look up the weather for a city in the demo's local database."""
    entry = WEATHER_DB.get(city.lower().strip())
    if entry is None:
        return {
            "status": "not_found",
            "city": city,
            "error": "City not in the local weather database. Try Oslo, Tokyo, London, Paris, New York, or Sydney.",
        }
    return {"status": "success", "city": city, **entry}
