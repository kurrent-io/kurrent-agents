"""KurrentDB → AG-UI event stream.

Async generator that reads canonical events from an ``AgentSession-{id}``
stream (catch-up + optional live tail) and yields AG-UI events as dicts.

The bridge is concerned only with I/O and decoding. The actual canonical →
AG-UI mapping lives in :mod:`kurrent_ag_ui.translator`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from kurrent_agent_schema import agent_session_stream
from kurrentdbclient import AsyncKurrentDBClient
from kurrentdbclient.exceptions import NotFoundError

from kurrent_ag_ui.translator import TranslateContext, translate


async def stream_session_events(
    client: AsyncKurrentDBClient,
    session_id: str,
    *,
    live: bool = False,
    custom_passthrough: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Yield AG-UI event dicts for ``session_id``.

    Reads from the start of ``AgentSession-{session_id}`` to the end (catch-up
    replay). When ``live=True``, switches to ``subscribe_to_stream`` after the
    catch-up read finishes, so newly appended events keep flowing.

    Yields nothing if the stream does not exist (NotFoundError swallowed).
    """
    stream = agent_session_stream(session_id)
    ctx = TranslateContext(
        thread_id=session_id,
        run_id=session_id,
        custom_passthrough=custom_passthrough,
    )

    if live:
        # subscribe_to_stream from the beginning gives us catch-up + live in
        # one pass. The async iterator does not return when the stream
        # reaches the end — it blocks for new appends.
        subscription = await client.subscribe_to_stream(stream)
        try:
            async for record in subscription:
                event_type = record.type
                payload = _decode(record.data)
                for ev in translate(event_type, payload, ctx):
                    yield ev
        finally:
            try:
                await subscription.stop()
            except Exception:
                pass
        return

    # Catch-up only.
    try:
        recorded = await client.read_stream(stream, resolve_links=True)
    except NotFoundError:
        return
    try:
        async for record in recorded:
            event_type = record.type
            payload = _decode(record.data)
            for ev in translate(event_type, payload, ctx):
                yield ev
    except NotFoundError:
        # Some client versions raise lazily during iteration.
        return


def _decode(data: bytes) -> dict[str, Any]:
    if not data:
        return {}
    return json.loads(data.decode("utf-8"))
