"""Background fact extraction via a persistent subscription on ``$all``.

A server-side-filtered persistent subscription watches every
``AgentSession-*`` stream and feeds ``UserMessageReceived`` events to a
pluggable :data:`FactExtractor`. Facts returned by the extractor are retained
via :class:`AgentMemory`.

Persistent (not catch-up) subscription is the point: KurrentDB retains the
acked position per group, so a service restart resumes from where it left off
rather than replaying the whole log and producing duplicate facts.

Mirrors the C# ``Kurrent.AgentFramework.Projections.FactExtractionService``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from kurrentdbclient import AsyncKurrentDBClient, RecordedEvent
from kurrentdbclient.exceptions import AlreadyExistsError, NotFoundError
from pydantic import ValidationError

from . import events as _events
from . import serialization
from .memory import AgentMemory

logger = logging.getLogger("kurrent_agent_framework.fact_extraction")

# Must match the C# StreamName.ForSession prefix.
_STREAM_PREFIX = "AgentSession-"

# Python client filter is a regex sequence (unlike the C#
# StreamFilter.Prefix helper). KurrentDB applies RE2 full-string matching on
# stream names, so we must cover the remainder of the name with ``.+``.
# Anchors (``^`` / ``$``) are implicit; adding them produces the same effect.
_STREAM_PREFIX_REGEX = r"AgentSession-.+"

# Backoff on subscription drop / transient failure; matches the C# 5s Task.Delay.
_RESUBSCRIBE_BACKOFF_SECONDS = 5.0


# A FactExtractor returns zero or more facts (plain strings) for a given user message.
FactExtractor = Callable[[str], Iterable[str]]


@dataclass(frozen=True)
class FactExtractionOptions:
    """Options for :class:`FactExtractionService`.

    Attributes:
        group_name: Persistent-subscription group name. The server retains the
            acked position per group, so the same group name survives
            restarts without reprocessing. Pick a unique name per deployment
            when running multiple fact extractors side by side.
        start_from_end: If ``True``, a freshly-created group begins at the log
            tip rather than the start. Only honoured on first creation; on
            subsequent restarts the server resumes from the last acked
            position either way. Default ``False`` (backfill existing
            sessions).
    """

    group_name: str = "FactExtraction"
    start_from_end: bool = False


class FactExtractionService:
    """Consumes ``AgentSession-`` events via a persistent subscription and
    extracts facts from ``UserMessageReceived`` events.

    Usage::

        service = FactExtractionService(client, memory, extractor)
        task = asyncio.create_task(service.run_forever())
        ...
        service.stop()
        await task

    ``run_forever`` returns only when :meth:`stop` is called or the current
    task is cancelled. On subscription drop or transient error, the loop
    backs off for ~5 seconds and re-subscribes.
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        memory: AgentMemory,
        extractor: FactExtractor,
        options: FactExtractionOptions = FactExtractionOptions(),
    ) -> None:
        self._client = client
        self._memory = memory
        self._extractor = extractor
        self._options = options
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Request graceful shutdown; the next ``run_forever`` iteration returns."""
        self._stop_event.set()

    async def run_forever(self) -> None:
        """Run the subscribe-consume loop until :meth:`stop` is called or cancelled."""
        while not self._stop_event.is_set():
            try:
                await self._ensure_subscription()
                await self._consume()
                # Subscription ended without throwing — server dropped it gracefully.
                if not self._stop_event.is_set():
                    logger.warning(
                        "Persistent subscription %s ended without error, re-subscribing in %ss",
                        self._options.group_name,
                        _RESUBSCRIBE_BACKOFF_SECONDS,
                    )
                    await self._sleep_or_stop(_RESUBSCRIBE_BACKOFF_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Persistent subscription %s interrupted, retrying in %ss",
                    self._options.group_name,
                    _RESUBSCRIBE_BACKOFF_SECONDS,
                )
                await self._sleep_or_stop(_RESUBSCRIBE_BACKOFF_SECONDS)

    async def _ensure_subscription(self) -> None:
        """Idempotent: use the existing group if present, else create it."""
        try:
            await self._client.get_subscription_info(self._options.group_name)
            return
        except NotFoundError:
            pass

        try:
            await self._client.create_subscription_to_all(
                self._options.group_name,
                from_end=self._options.start_from_end,
                filter_include=[_STREAM_PREFIX_REGEX],
                filter_by_stream_name=True,
            )
            logger.info(
                "Created persistent subscription %s on $all with prefix filter %s",
                self._options.group_name,
                _STREAM_PREFIX,
            )
        except AlreadyExistsError:
            # Another instance created it concurrently.
            logger.debug(
                "Persistent subscription %s already exists (created concurrently)",
                self._options.group_name,
            )

    async def _consume(self) -> None:
        """Read the subscription, ack on success, nack(retry) on exception."""
        subscription = await self._client.read_subscription_to_all(
            self._options.group_name
        )
        async with subscription:
            async for event in subscription:
                if self._stop_event.is_set():
                    break
                try:
                    await self._process(event)
                    await subscription.ack(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Fact extraction failed for %s/%s, nacking for retry: %r",
                        event.stream_name,
                        event.stream_position,
                        exc,
                    )
                    await subscription.nack(event, action="retry")

    async def _process(self, event: RecordedEvent) -> None:
        """Extract facts from a UserMessageReceived event; ignore the rest."""
        if event.type != "UserMessageReceived":
            return
        try:
            domain_event = serialization.deserialize(event)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError):
            return
        if not isinstance(domain_event, _events.UserMessageReceived):
            return
        content = (domain_event.content or "").strip()
        if not content:
            return

        for fact in self._extractor(domain_event.content or ""):
            if not fact or not fact.strip():
                continue
            logger.debug(
                "Auto-extracted fact from %s:%s: %s",
                event.stream_name,
                event.stream_position,
                fact,
            )
            await self._memory.retain(fact)

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep ``seconds`` but wake immediately if ``stop()`` is called."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass


@asynccontextmanager
async def run_fact_extraction(
    client: AsyncKurrentDBClient,
    memory: AgentMemory,
    extractor: FactExtractor,
    options: FactExtractionOptions = FactExtractionOptions(),
) -> AsyncIterator[FactExtractionService]:
    """Spawn a :class:`FactExtractionService` as a background task for the
    lifetime of the ``async with`` block.

    Usage::

        async with run_fact_extraction(client, memory, extractor) as service:
            # run your agent; facts are extracted in the background
            ...

    On exit (normal or exceptional), the background task is signalled to stop,
    cancelled, and awaited so callers never leave a dangling task behind.
    """
    service = FactExtractionService(client, memory, extractor, options)
    task = asyncio.create_task(service.run_forever(), name="fact_extraction.run_forever")
    try:
        yield service
    finally:
        service.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Only swallow the CancelledError that came from our own
            # ``task.cancel()`` above. If the caller task is itself being
            # cancelled, re-raise so outer cancellation / timeouts aren't
            # silently dropped.
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                raise
