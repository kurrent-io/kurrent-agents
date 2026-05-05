"""Drift-detection tests for the OpenAI Agents Python write + read path.

For every canonical fixture under ``schema/fixtures/events/``:

1. Parse into the canonical record via ``EVENT_TYPE_BY_NAME``.
2. Pass through :func:`serialization.serialize` (write path).
3. Append to KurrentDB.
4. Read back, deserialise, and re-serialise the round-tripped record.
5. Assert structural equality with the original fixture.

Pairs with the schema package's per-fixture round-trip test and with the
matching MAF Python / Strands integrations' tests, so a future schema
change cannot silently bypass the OpenAI Agents codec.

Also exercises a bespoke lossless-round-trip for the
``extensions.openai.raw_item`` contract: ingest an OpenAI Agents item →
decompose to canonical → re-compose → compare to the original dict.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from google.protobuf.message import Message
from kurrent_agent_schema import EVENT_TYPE_BY_NAME, from_json
from kurrentdbclient import AsyncKurrentDBClient, StreamState

from kurrent_openai_agents import _serialization
from kurrent_openai_agents._codec import canonical_to_items, items_to_canonical


def _locate_fixtures_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "schema" / "fixtures"
        if (candidate / "events").is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate schema/fixtures relative to this test tree."
    )


FIXTURES_ROOT = _locate_fixtures_root()
EVENTS_DIR = FIXTURES_ROOT / "events"


def _normalise(obj):
    if isinstance(obj, dict):
        return {k: _normalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_normalise(x) for x in obj]
    return obj


def _fixture_cases() -> list[Path]:
    return sorted(EVENTS_DIR.glob("*.json"))


def _parse(fixture_path: Path) -> tuple[dict, Message]:
    raw = fixture_path.read_text(encoding="utf-8")
    original = json.loads(raw)
    model = EVENT_TYPE_BY_NAME.get(fixture_path.stem)
    assert model is not None, f"No canonical model registered for '{fixture_path.stem}'"
    parsed = from_json(model, raw)
    return original, parsed


def test_every_canonical_event_has_a_fixture() -> None:
    missing = [
        name for name in EVENT_TYPE_BY_NAME
        if not (EVENTS_DIR / f"{name}.json").exists()
    ]
    assert not missing, f"Missing fixtures for canonical events: {missing}"


@pytest.mark.parametrize("fixture_path", _fixture_cases(), ids=lambda p: p.stem)
def test_fixture_codec_round_trip(fixture_path: Path) -> None:
    """Pure-codec round-trip — no KurrentDB."""
    original, parsed = _parse(fixture_path)

    new_event = _serialization.serialize(parsed)
    written = json.loads(new_event.data)
    assert _normalise(written) == _normalise(original), (
        f"Drift on serialise for {fixture_path.stem}: "
        f"{json.dumps(_normalise(written))} != {json.dumps(_normalise(original))}"
    )


@pytest.mark.parametrize("fixture_path", _fixture_cases(), ids=lambda p: p.stem)
async def test_fixture_kurrentdb_round_trip(
    fixture_path: Path, kurrentdb_client: AsyncKurrentDBClient
) -> None:
    """Server-side round-trip — write fixture, read it back, re-serialise, compare."""
    original, parsed = _parse(fixture_path)

    new_event = _serialization.serialize(parsed)
    stream = f"FixtureRoundTrip-{fixture_path.stem}-{uuid.uuid4().hex[:8]}"
    await kurrentdb_client.append_to_stream(
        stream, events=[new_event], current_version=StreamState.NO_STREAM,
    )

    [recorded] = await kurrentdb_client.get_stream(stream)
    restored = _serialization.deserialize(recorded)
    assert restored is not None

    re_serialised = json.loads(_serialization.serialize(restored).data)
    assert _normalise(re_serialised) == _normalise(original), (
        f"Drift on KurrentDB round-trip for {fixture_path.stem}"
    )


# ----- raw_item lossless contract -------------------------------------------


_RAW_ITEM_FIXTURES: list[dict] = [
    {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hello"}],
    },
    {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hi"}],
    },
    {
        "type": "function_call",
        "call_id": "c1",
        "name": "search",
        "arguments": '{"q": "x"}',
    },
    {
        "type": "function_call_output",
        "call_id": "c1",
        "output": '{"hits": 3}',
    },
    {
        "type": "reasoning",
        "id": "r1",
        "content": [{"type": "reasoning_text", "text": "because..."}],
    },
    {
        "type": "reasoning",
        "id": "r2",
        "encrypted_content": "AAA-OPAQUE-AAA",
        "signature": "sig",
    },
    {
        "type": "mcp_approval_request",
        "id": "req-1",
        "name": "publish_post",
        "arguments": '{"title": "hi"}',
        "server_label": "blog-mcp",
    },
    {
        "type": "mcp_approval_response",
        "approval_request_id": "req-1",
        "approve": True,
    },
    {
        "type": "handoff_call",
        "call_id": "h1",
        "name": "handoff_to_expert",
        "arguments": "{}",
    },
    {
        "type": "computer_call",
        "call_id": "comp1",
        "action": "screenshot",
    },
]


@pytest.mark.parametrize("item", _RAW_ITEM_FIXTURES, ids=lambda it: it["type"])
def test_raw_item_round_trip(item: dict) -> None:
    """Decompose → canonical event(s) → re-compose → original dict."""
    events = items_to_canonical([item], start_index=0, timestamp=datetime.now(UTC))
    restored = canonical_to_items(events)
    assert restored == [item]
