"""Tests for the OpenAI Agents serialization helpers."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from kurrent_agent_schema import SubagentStarted

from kurrent_openai_agents._serialization import serialize, serialize_for_multi_append


def _make_started() -> SubagentStarted:
    evt = SubagentStarted(agent_id="sub-x-abc", agent_type="X")
    evt.timestamp.FromDatetime(datetime(2026, 5, 14, 12, 0, 0))
    return evt


def test_single_stream_serialize_stamps_schema_version_as_int() -> None:
    new_event = serialize(_make_started())
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == 2
    assert isinstance(metadata["$schema_version"], int)


def test_multi_append_serialize_stamps_schema_version_as_string() -> None:
    new_event = serialize_for_multi_append(_make_started())
    metadata = json.loads(new_event.metadata)
    assert metadata["$schema_version"] == "2"
    assert isinstance(metadata["$schema_version"], str)


def test_multi_append_rejects_non_string_metadata_values() -> None:
    with pytest.raises(ValueError, match="multi-append metadata"):
        serialize_for_multi_append(_make_started(), metadata={"latency_ms": 42})


def test_multi_append_passes_through_string_metadata() -> None:
    new_event = serialize_for_multi_append(
        _make_started(), metadata={"source": "test"}
    )
    metadata = json.loads(new_event.metadata)
    assert metadata["source"] == "test"
    assert metadata["$schema_version"] == "2"
