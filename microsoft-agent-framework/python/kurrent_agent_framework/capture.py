"""Capture token-usage data from chat-client invocations.

This mirrors ``Kurrent.AgentFramework.Capture.UsageCapture`` on the .NET side:
a :class:`ChatMiddleware` that intercepts chat responses, extracts
``UsageDetails``, and stashes them keyed by ``message_id`` so
:class:`KurrentDBHistoryProvider` can attach ``$usage`` metadata to the
assistant message event on save.

Usage is keyed by ``message_id`` for correct correlation in tool-calling
loops. Not thread-safe — both capture and read happen sequentially within a
single agent invocation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_framework import ChatContext, ChatMiddleware, ChatResponse, UsageDetails


class UsageCapture(ChatMiddleware):
    """Chat middleware that records ``UsageDetails`` keyed by message id.

    Usage is exposed through :meth:`try_get` so the history provider can
    attach ``$usage`` metadata to the assistant event on save. Supports both
    non-streaming (``context.result`` is a :class:`ChatResponse`) and
    streaming (``context.result`` is a ``ResponseStream``) paths.

    Examples:
        .. code-block:: python

            from agent_framework import ChatClientAgent
            from kurrent_agent_framework import KurrentDBHistoryProvider, UsageCapture

            capture = UsageCapture()
            history = KurrentDBHistoryProvider(client, usage_capture=capture)
            agent = ChatClientAgent(
                chat_client=client,
                middleware=[capture],
                history_provider=history,
            )
    """

    def __init__(self) -> None:
        self._usages: dict[str, UsageDetails] = {}

    def try_get(self, message_id: str) -> UsageDetails | None:
        """Return the captured usage for ``message_id`` or ``None``."""
        return self._usages.get(message_id)

    def clear(self) -> None:
        """Drop every captured usage record. Called by the history provider
        after each save so the map does not grow across turns."""
        self._usages.clear()

    async def process(
        self,
        context: ChatContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.stream:
            # The pipeline wires stream_result_hooks onto the returned stream
            # once our process() completes. The hook fires after the stream is
            # fully consumed and the final ChatResponse is built — by which
            # point message ids and aggregated usage_details are populated.
            context.stream_result_hooks.append(self._on_stream_result)
            await call_next()
            return

        await call_next()
        if isinstance(context.result, ChatResponse):
            self._capture_from_response(context.result)

    def _on_stream_result(self, response: ChatResponse) -> ChatResponse:
        self._capture_from_response(response)
        return response

    def _capture_from_response(self, response: ChatResponse) -> None:
        usage = response.usage_details
        if not usage:
            return
        for msg in response.messages:
            if msg.message_id:
                self._usages[msg.message_id] = usage
