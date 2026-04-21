"""Token usage metadata model.

Usage rides on KurrentDB event metadata under the ``$usage`` key, on every
assistant event (``AssistantTextGenerated``, ``AssistantToolCallsGenerated``,
``AssistantThinkingGenerated``). It is *not* a canonical event payload.

See ``schema/SCHEMA_v2.md §3.6``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

USAGE_METADATA_KEY: str = "$usage"
"""KurrentDB metadata key under which ``TokenUsage`` is written."""


class TokenUsage(BaseModel):
    """Token counts reported by the model provider for one assistant event.

    All fields optional — providers differ in which counts they return.
    For ``AssistantThinkingGenerated``, populate ``reasoning_tokens``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
