"""Token usage metadata constants.

Usage rides on KurrentDB event metadata under the ``$usage`` key, on every
assistant event (``AssistantTextGenerated``, ``AssistantToolCallsGenerated``,
``AssistantThinkingGenerated``). It is *not* a canonical event payload.

The ``TokenUsage`` message itself is generated from
``schema/proto/kurrent/agent/v2/usage.proto`` and re-exported from the package
top level.

See ``schema/SCHEMA_v2.md §3.6``.
"""

from __future__ import annotations

USAGE_METADATA_KEY: str = "$usage"
"""KurrentDB metadata key under which ``TokenUsage`` is written."""
