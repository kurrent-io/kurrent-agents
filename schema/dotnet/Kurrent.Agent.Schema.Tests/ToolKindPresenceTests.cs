using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

/// <summary>
/// <c>ToolCallInfo.ToolKind</c>'s load-bearing semantic (SCHEMA_v2.md §3.4.1):
/// an ABSENT kind ("nobody classified this call") must stay distinguishable
/// from <c>"other"</c> ("classified, and none of the above"). Collapsing the two
/// is what would stop a consumer trusting the field, so the distinction is
/// pinned here rather than left to rest on the writer settings.
/// </summary>
public class ToolKindPresenceTests {
    [Fact]
    public void Unclassified_call_omits_tool_kind_entirely() {
        var call = new ToolCallInfo { CallId = "call-1", ToolName = "unmapped_vendor_tool" };

        Assert.False(call.HasToolKind);
        Assert.DoesNotContain("tool_kind", SchemaJsonOptions.ToJson(call));
    }

    [Fact]
    public void Other_is_written_and_read_back_as_a_present_value() {
        var call = new ToolCallInfo { CallId = "call-1", ToolName = "Task", ToolKind = "other" };
        var json = SchemaJsonOptions.ToJson(call);

        Assert.Contains("\"tool_kind\": \"other\"", json);

        var parsed = SchemaJsonOptions.FromJson<ToolCallInfo>(json);
        Assert.True(parsed.HasToolKind);
        Assert.Equal("other", parsed.ToolKind);
    }

    [Fact]
    public void Absent_and_other_do_not_round_trip_onto_each_other() {
        var absent = SchemaJsonOptions.FromJson<ToolCallInfo>(
            "{\"call_id\":\"call-1\",\"tool_name\":\"unmapped_vendor_tool\"}");
        var other = SchemaJsonOptions.FromJson<ToolCallInfo>(
            "{\"call_id\":\"call-1\",\"tool_name\":\"unmapped_vendor_tool\",\"tool_kind\":\"other\"}");

        Assert.False(absent.HasToolKind);
        Assert.True(other.HasToolKind);
        Assert.NotEqual(absent, other);
    }

    [Fact]
    public void Explicit_empty_string_is_still_presence_not_absence() {
        // Producers must omit the field rather than write "" (SCHEMA_v2.md §3.4.1).
        // This test documents what happens if one ignores that: the empty string
        // survives as a SET value, so it is not silently laundered into "absent".
        var call = new ToolCallInfo { CallId = "call-1", ToolName = "x", ToolKind = "" };

        Assert.True(call.HasToolKind);
        Assert.Contains("tool_kind", SchemaJsonOptions.ToJson(call));
    }
}
