"""Background fact extraction demo.

Wires up :func:`kurrent_agent_framework.run_fact_extraction` with a BYO
regex-based ``FactExtractor`` (personal-assistant-style patterns), writes a
few user messages into an ``AgentSession-*`` stream, and prints the facts the
background task retained into ``AgentMemory``.

The framework ships no baked-in extractor — the regex patterns below live in
sample code for exactly the reason DEV-1449 moved them out of the framework:
what counts as a "fact" is domain-specific.

Prereq: KurrentDB at ``kurrentdb://localhost:2113?Tls=false``. Run
``docker compose up -d`` from ``microsoft-agent-framework/python/`` (the
directory containing ``docker-compose.yml``, one level above this sample).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from kurrentdbclient import AsyncKurrentDBClient, StreamState

from kurrent_agent_framework import (
    FactExtractionOptions,
    KurrentDBAgentMemory,
    events,
    run_fact_extraction,
    serialization,
    stream_name,
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:my name is|i'm|i am)\s+(\w+)", re.IGNORECASE), "User's name is {0}"),
    (re.compile(r"i (?:work|am working) (?:at|for)\s+(.+?)(?:\.|,|$)", re.IGNORECASE), "User works at {0}"),
    (re.compile(r"i (?:prefer|like|love|use)\s+(.+?)(?:\.|,|$)", re.IGNORECASE), "User prefers {0}"),
    (re.compile(r"i (?:live|am based|am located) (?:in|at)\s+(.+?)(?:\.|,|$)", re.IGNORECASE), "User lives in {0}"),
    (re.compile(r"my (?:email|e-mail) (?:is|address is)\s+(\S+)", re.IGNORECASE), "User's email is {0}"),
    (re.compile(r"my (?:timezone|time zone|tz) is\s+(.+?)(?:\.|,|$)", re.IGNORECASE), "User's timezone is {0}"),
]


def personal_fact_extractor(message: str) -> Iterable[str]:
    """Pull personal facts (name, employer, preferences, …) from a user message.

    Mirrors the C# ``BasicAgent`` sample's ``PersonalFactExtractor``. Domain-specific;
    real applications should write their own.
    """
    for pattern, template in _PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        extracted = match.group(1).strip()
        if extracted:
            yield template.format(extracted)


async def _append_user_message(
    client: AsyncKurrentDBClient, stream: str, index: int, content: str
) -> None:
    now = datetime.now(UTC)
    await client.append_to_stream(
        stream,
        events=[
            serialization.serialize(
                events.UserMessageReceived(
                    content=content,
                    message_id=f"m-{index}",
                    author_name="user",
                    created_at=now,
                    message_index=index,
                    timestamp=now,
                )
            )
        ],
        current_version=StreamState.ANY,
    )


async def main() -> None:
    client = AsyncKurrentDBClient("kurrentdb://localhost:2113?Tls=false")
    await client.connect()

    session_id = uuid.uuid4().hex
    stream = stream_name.for_session(session_id)
    memory_stream = f"AgentMemory-demo-{session_id}"
    memory = KurrentDBAgentMemory(client, stream_name=memory_stream)

    try:
        # Fresh group + start_from_end keeps this demo self-contained: it only
        # processes the messages we append below, ignoring whatever else lives
        # in the log from earlier demo runs.
        options = FactExtractionOptions(
            group_name=f"FactExtraction-demo-{session_id}",
            start_from_end=True,
        )
        async with run_fact_extraction(client, memory, personal_fact_extractor, options):
            # Subscription needs a beat to register before the first append,
            # otherwise the backfill-from-start race can miss early events.
            await asyncio.sleep(0.2)

            messages = [
                "Hi! My name is Alexey.",
                "I work at Kurrent.",
                "I prefer dark mode IDEs.",
                "My email is alexey@example.com.",
                "Weather's nice today, isn't it?",  # no facts
            ]
            for i, content in enumerate(messages):
                await _append_user_message(client, stream, i, content)

            # Give the background task time to drain + retain.
            await asyncio.sleep(1.0)

        print(f"\n=== Retained facts in {memory_stream} ===\n")
        async for fact in memory.recall(""):
            print(f"  - {fact}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
