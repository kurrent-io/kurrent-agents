"""``UsageCapturePlugin`` — see ``DESIGN.md`` §7.5.

The session service emits ``$usage`` KurrentDB event metadata on assistant
events automatically (pulled from ``Event.usage_metadata``). This plugin is
optional and exists for application-level observability hooks — logging,
Prometheus counters, cost estimation — giving parity with the .NET
``UsageCapture`` middleware users will recognise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_response import LlmResponse


class UsageCapturePlugin(BasePlugin):
    """Observability plugin over ``LlmResponse.usage_metadata``.

    Does not replace the response (always returns ``None``). Override or
    subclass to integrate with your logging / metrics pipeline.
    """

    def __init__(self, name: str = "kurrent_usage_capture") -> None:
        super().__init__(name=name)

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        # TODO: emit usage to logs/metrics. Implementation deferred.
        return None
