"""Drift-detection tests for the Python canonical-types package.

For every fixture under ``schema/fixtures/events/``, parse the JSON via the
sanctioned helper, reserialise, and assert structural equality. The matching
.NET package runs an equivalent test against the same fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kurrent_agent_schema import TokenUsage, from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS_DIR = FIXTURES_ROOT / "events"
METADATA_DIR = FIXTURES_ROOT / "metadata"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _normalise(obj):
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
    cls = EVENT_TYPE_BY_NAME.get(event_name)
    assert cls is not None, f"No canonical model registered for '{event_name}'"

    original = _load(fixture_path)
    parsed = from_json(cls, fixture_path.read_text())
    serialised = json.loads(to_json(parsed))

    assert _normalise(serialised) == _normalise(original), (
        f"Round-trip drift for {event_name}: serialised form does not match fixture"
    )


def test_every_canonical_event_has_a_fixture() -> None:
    missing = [
        name for name in EVENT_TYPE_BY_NAME
        if not (EVENTS_DIR / f"{name}.json").exists()
    ]
    assert not missing, f"Missing fixtures for canonical events: {missing}"


def test_usage_metadata_round_trip() -> None:
    fixture_path = METADATA_DIR / "usage.json"
    original = _load(fixture_path)
    parsed = from_json(TokenUsage, fixture_path.read_text())
    serialised = json.loads(to_json(parsed))
    assert _normalise(serialised) == _normalise(original)
