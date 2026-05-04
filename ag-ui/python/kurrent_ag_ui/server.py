"""Starlette + sse-starlette wrapper around the KurrentDB → AG-UI bridge.

``GET /sessions/{session_id}/events`` streams AG-UI events as SSE.
``?live=true`` keeps the stream open after catch-up, tailing live appends.

Run with::

    uvicorn kurrent_ag_ui.server:app
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

from kurrentdbclient import AsyncKurrentDBClient
from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route

from kurrent_ag_ui.bridge import stream_session_events

DEFAULT_CONN = "kurrentdb://localhost:2113?Tls=false"


def make_app(connection_string: str | None = None) -> Starlette:
    conn = connection_string or os.environ.get("KURRENTDB_CONNECTION_STRING", DEFAULT_CONN)

    async def session_events(request: Request) -> EventSourceResponse:
        session_id = request.path_params["session_id"]
        live = request.query_params.get("live", "").lower() in ("1", "true", "yes")

        async def gen() -> AsyncIterator[dict]:
            client = AsyncKurrentDBClient(conn)
            try:
                async for ev in stream_session_events(client, session_id, live=live):
                    # sse-starlette accepts {"event": ..., "data": ...}; we put
                    # the AG-UI ``type`` on the ``event`` field so EventSource
                    # consumers can route on it, and the full payload as data.
                    yield {"event": ev["type"], "data": json.dumps(ev)}
            finally:
                try:
                    await client.close()
                except Exception:
                    pass

        return EventSourceResponse(gen())

    return Starlette(
        debug=False,
        routes=[Route("/sessions/{session_id}/events", session_events)],
    )


app = make_app()
