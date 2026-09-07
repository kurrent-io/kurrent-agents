"""``ToolCallInfo.tool_kind``'s load-bearing semantic (SCHEMA_v2.md §3.4.1).

An ABSENT kind ("nobody classified this call") must stay distinguishable from
``"other"`` ("classified, and none of the above"). Collapsing the two is what
would stop a consumer trusting the field, so the distinction is pinned here
rather than left to rest on the writer settings. The .NET package runs an
equivalent suite (``ToolKindPresenceTests``).
"""

from __future__ import annotations

import json

from kurrent_agent_schema import ToolCallInfo, from_json, to_json


def test_unclassified_call_omits_tool_kind_entirely() -> None:
    call = ToolCallInfo(call_id="call-1", tool_name="unmapped_vendor_tool")

    assert not call.HasField("tool_kind")
    assert "tool_kind" not in json.loads(to_json(call))


def test_other_is_written_and_read_back_as_a_present_value() -> None:
    call = ToolCallInfo(call_id="call-1", tool_name="Task", tool_kind="other")
    payload = to_json(call)

    assert json.loads(payload)["tool_kind"] == "other"

    parsed = from_json(ToolCallInfo, payload)
    assert parsed.HasField("tool_kind")
    assert parsed.tool_kind == "other"


def test_absent_and_other_do_not_round_trip_onto_each_other() -> None:
    absent = from_json(
        ToolCallInfo, '{"call_id": "call-1", "tool_name": "unmapped_vendor_tool"}'
    )
    other = from_json(
        ToolCallInfo,
        '{"call_id": "call-1", "tool_name": "unmapped_vendor_tool", "tool_kind": "other"}',
    )

    assert not absent.HasField("tool_kind")
    assert other.HasField("tool_kind")
    assert absent != other


def test_explicit_empty_string_is_still_presence_not_absence() -> None:
    # Producers must omit the field rather than write "" (SCHEMA_v2.md §3.4.1).
    # This test documents what happens if one ignores that: the empty string
    # survives as a SET value, so it is not silently laundered into "absent".
    call = ToolCallInfo(call_id="call-1", tool_name="x", tool_kind="")

    assert call.HasField("tool_kind")
    assert "tool_kind" in json.loads(to_json(call))
