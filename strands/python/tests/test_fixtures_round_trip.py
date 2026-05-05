"""Drift-detection tests for the Strands write + read path.

For every canonical fixture under ``schema/fixtures/events/``:

1. Parse into the canonical record via :data:`EVENT_TYPE_BY_NAME`.
2. :func:`serialization.serialize` the record (Strands write path).
3. Append to KurrentDB.
4. Read the stream back and :func:`serialization.deserialize` into a record.
5. Re-serialise the round-tripped record and assert structural equality with
   the original fixture.

This pins both:

- Structural JSON parity with the fixtures the MAF (.NET / Python) and shared
  ``kurrent-agent-schema`` packages also round-trip — Strands is just another
  consumer of that contract. Equality is computed after canonicalising both
  sides (key sort) so property-order differences between
  ``google.protobuf.json_format`` invocations do not cause false positives;
  raw UTF-8 is not guaranteed to match byte-for-byte.
- The Strands ``serialization`` adapter preserves the event payload structure
  end-to-end and stamps ``$schema_version = 2`` on metadata (SCHEMA_v2 §9).

Strands' ``SessionManager`` callbacks are sync, so this suite uses the sync
``KurrentDBClient`` fixture (mirrors the integration's runtime).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from google.protobuf.message import Message
from kurrent_agent_schema import EVENT_TYPE_BY_NAME, from_json
from kurrentdbclient import KurrentDBClient, StreamState

from kurrent_strands import _serialization as serialization


def _locate_fixtures_root() -> Path:
    """Walk up from this test file until a directory containing
    ``schema/fixtures/events`` is found. Mirrors the MAF Python / .NET
    discovery so a repo relayout does not silently break fixture lookup the
    way a hard-coded ``parents[N]`` hop would."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "schema" / "fixtures"
        if (candidate / "events").is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate schema/fixtures relative to the Strands test tree."
    )


FIXTURES_ROOT = _locate_fixtures_root()
EVENTS_DIR = FIXTURES_ROOT / "events"


def _normalise(obj):
    """Sort dict keys recursively so key-ordering differences don't fail equality."""
    if isinstance(obj, dict):
        return {k: _normalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_normalise(x) for x in obj]
    return obj


def _fixture_cases() -> list[Path]:
    return sorted(EVENTS_DIR.glob("*.json"))


def _parse(fixture_path: Path) -> tuple[dict, Message]:
    # Pin UTF-8 so the drift tests stay deterministic on non-UTF-8 locales —
    # at least one fixture (AssistantTextGenerated.json) contains ``°C``.
    raw = fixture_path.read_text(encoding="utf-8")
    original = json.loads(raw)
    model = EVENT_TYPE_BY_NAME.get(fixture_path.stem)
    assert model is not None, f"No canonical model registered for '{fixture_path.stem}'"
    parsed = from_json(model, raw)
    return original, parsed


def test_every_canonical_event_has_a_fixture() -> None:
    """Parity guard: the shared schema's drift test already asserts this from
    the package side, but we also want CI here to fail loudly if a future
    schema change lands an event without a fixture and the Strands write path
    silently stops exercising it."""
    missing = [
        name for name in EVENT_TYPE_BY_NAME
        if not (EVENTS_DIR / f"{name}.json").exists()
    ]
    assert not missing, f"Missing fixtures for canonical events: {missing}"


@pytest.mark.parametrize("fixture_path", _fixture_cases(), ids=lambda p: p.stem)
def test_fixture_codec_round_trip(fixture_path: Path) -> None:
    """Pure-codec round-trip (no KurrentDB): serialize → JSON → deserialize →
    re-serialize, assert structural equality with the fixture. The server-side
    variant below exercises the full write+read cycle through KurrentDB."""
    original, parsed = _parse(fixture_path)

    new_event = serialization.serialize(parsed)
    written = json.loads(new_event.data)
    assert _normalise(written) == _normalise(original), (
        f"Serialize drift for {fixture_path.stem}: written JSON does not match fixture"
    )

    # Metadata stamping — SCHEMA_v2 §9.
    metadata = json.loads(new_event.metadata)
    assert metadata == {"$schema_version": 2}

    # Wire type matches the fixture filename (round-trips via EVENT_TYPE_BY_NAME).
    assert new_event.type == fixture_path.stem


@pytest.mark.parametrize("fixture_path", _fixture_cases(), ids=lambda p: p.stem)
def test_fixture_round_trips_through_kurrentdb(
    fixture_path: Path, kurrentdb_client: KurrentDBClient
) -> None:
    """End-to-end write+read through KurrentDB — pairs the codec round-trip
    above with the actual sync client Strands' ``SessionManager`` uses at
    runtime, so the two integrations stay in lock-step with MAF on the wire."""
    original, parsed = _parse(fixture_path)

    new_event = serialization.serialize(parsed)
    stream = f"AgentSession-test-{uuid.uuid4().hex}"

    kurrentdb_client.append_to_stream(
        stream_name=stream,
        current_version=StreamState.NO_STREAM,
        events=[new_event],
    )

    # ``read_stream`` opens a server-streaming gRPC RPC; close it deterministically
    # via the context manager so the underlying channel doesn't linger across the
    # 18 parametrised cases waiting on GC finalisation.
    with kurrentdb_client.read_stream(stream) as response:
        recorded = next(response, None)
    assert recorded is not None, f"Stream {stream} was empty after append"

    decoded = serialization.deserialize(recorded)
    assert decoded is not None, f"Deserialize returned None for {recorded.type}"

    # Re-serialise through the same write path and assert structural parity with the fixture.
    reserialised = json.loads(serialization.serialize(decoded).data)
    assert _normalise(reserialised) == _normalise(original), (
        f"Read-back drift for {fixture_path.stem}: re-serialised form does not match fixture"
    )

    # Server-stored metadata must also carry $schema_version.
    assert recorded.metadata, f"Metadata empty for {fixture_path.stem}"
    stored_metadata = json.loads(recorded.metadata)
    assert stored_metadata.get("$schema_version") == 2
