"""Minimal end-to-end round-trip against a running KurrentDB instance.

Writes a few messages through ``KurrentDBHistoryProvider``, reads them back, and
dumps the raw events. Validates that the wire format is identical to what the
C# BasicAgent sample writes.

Prereq: a KurrentDB instance at ``kurrentdb://localhost:2113?Tls=false``
(use the docker-compose.yml in the C# repo, or: ``docker run -p 2113:2113
docker.kurrent.io/kurrent-latest/kurrentdb:latest --insecure --run-projections=All``).
"""

from __future__ import annotations

import asyncio
import uuid

from agent_framework import Content, Message
from kurrent_agent_schema import agent_session_stream
from kurrentdbclient import AsyncKurrentDBClient

from kurrent_agent_framework import KurrentDBHistoryProvider


async def main() -> None:
    session_id = str(uuid.uuid4())
    client = AsyncKurrentDBClient("kurrentdb://localhost:2113?Tls=false")
    await client.connect()

    try:
        history = KurrentDBHistoryProvider(
            client,
            source_id="demo",
            agent_name="PythonDemoAgent",
            model_name="synthetic",
        )

        # --- save some synthetic messages ---
        await history.save_messages(
            session_id,
            [
                Message(role="user", contents=[Content(type="text", text="Hi there!")]),
                Message(
                    role="assistant",
                    contents=[Content(type="text", text="Hello! How can I help?")],
                ),
                Message(
                    role="assistant",
                    contents=[
                        Content(
                            type="function_call",
                            call_id="c1",
                            name="get_weather",
                            arguments={"city": "London"},
                        )
                    ],
                ),
                Message(
                    role="tool",
                    contents=[
                        Content(type="function_result", call_id="c1", result="Sunny, 22C")
                    ],
                ),
                Message(
                    role="assistant",
                    contents=[Content(type="text", text="It's sunny in London.")],
                ),
            ],
        )
        await history.end_session(session_id, reason="completed")

        # --- read back as Message objects ---
        print(f"\n=== Reconstructed messages for session {session_id} ===\n")
        for msg in await history.get_messages(session_id):
            print(f"  [{msg.role}] {msg.text or ''}")
            for content in msg.contents:
                if content.type == "function_call":
                    print(f"    -> call {content.name}({content.arguments})")
                elif content.type == "function_result":
                    print(f"    <- result for {content.call_id}: {content.result}")

        # --- dump raw events ---
        print(f"\n=== Raw events in {agent_session_stream(session_id)} ===\n")
        response = await client.read_stream(agent_session_stream(session_id))
        async for recorded in response:
            print(f"  [{recorded.stream_position}] {recorded.type}")
            print(f"       {recorded.data.decode('utf-8')}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
