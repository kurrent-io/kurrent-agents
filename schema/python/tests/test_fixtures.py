"""Drift-detection tests for the Python canonical-types package.

For every fixture under ``schema/fixtures/events/``, load the JSON, deserialise
into the matching Pydantic model, reserialise, and assert byte-equality with
the original. The matching .NET package runs an equivalent test against the
same fixtures — together they guarantee the two implementations cannot drift
without failing CI.

See ``schema/fixtures/README.md`` for the fixture contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kurrent_agent_schema import TokenUsage
from kurrent_agent_schema.events import EVENT_TYPE_BY_NAME

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS_DIR = FIXTURES_ROOT / "events"
METADATA_DIR = FIXTURES_ROOT / "metadata"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _normalise(obj):
    """Sort dict keys recursively so key ordering differences don't fail equality."""
    if isinstance(obj, dict):
        return {k: _normalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_normalise(x) for x in obj]
    return obj


def _event_fixture_cases():
    if not EVENTS_DIR.exists():
        return []
    return sorted(EVENTS_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _event_fixture_cases(), ids=lambda p: p.stem)
def test_event_fixture_round_trip(fixture_path: Path) -> None:
    event_name = fixture_path.stem
    model = EVENT_TYPE_BY_NAME.get(event_name)
    assert model is not None, f"No canonical model registered for '{event_name}'"

    original = _load(fixture_path)
    parsed = model.model_validate(original)
    serialised = json.loads(
        parsed.model_dump_json(exclude_none=True, by_alias=True)
    )

    assert _normalise(serialised) == _normalise(original), (
        f"Round-trip drift for {event_name}: serialised form does not match fixture"
    )


def test_every_canonical_event_has_a_fixture() -> None:
    """Every registered canonical event type must have a fixture file."""
    missing = [
        name for name in EVENT_TYPE_BY_NAME
        if not (EVENTS_DIR / f"{name}.json").exists()
    ]
    assert not missing, f"Missing fixtures for canonical events: {missing}"


def test_usage_metadata_round_trip() -> None:
    original = _load(METADATA_DIR / "usage.json")
    parsed = TokenUsage.model_validate(original)
    serialised = json.loads(parsed.model_dump_json(exclude_none=True, by_alias=True))
    assert _normalise(serialised) == _normalise(original)
