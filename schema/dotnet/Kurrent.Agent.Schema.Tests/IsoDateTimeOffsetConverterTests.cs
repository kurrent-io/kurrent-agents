using System.Text.Json;
using Kurrent.Agent.Schema;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

/// <summary>
/// Unit tests for the custom <c>DateTimeOffset</c> converter used by
/// <see cref="SchemaJsonOptions"/>. Not fixture-based — directly exercises
/// round-trip behaviour across UTC, non-UTC, and naive inputs.
/// </summary>
public class IsoDateTimeOffsetConverterTests {
    record Wrapper(DateTimeOffset When);

    [Fact]
    public void Utc_round_trips_with_Z_suffix() {
        const string json = "{\"when\":\"2026-04-21T10:00:00Z\"}";
        var parsed = JsonSerializer.Deserialize<Wrapper>(json, SchemaJsonOptions.Default)!;
        Assert.Equal(TimeSpan.Zero, parsed.When.Offset);

        var serialised = JsonSerializer.Serialize(parsed, SchemaJsonOptions.Default);
        Assert.Equal(json, serialised);
    }

    [Fact]
    public void Non_utc_offset_is_preserved_across_round_trip() {
        const string json = "{\"when\":\"2026-04-21T12:00:00+02:00\"}";
        var parsed = JsonSerializer.Deserialize<Wrapper>(json, SchemaJsonOptions.Default)!;
        Assert.Equal(TimeSpan.FromHours(2), parsed.When.Offset);

        var serialised = JsonSerializer.Serialize(parsed, SchemaJsonOptions.Default);
        Assert.Contains("+02:00", serialised);
        // Instant also preserved — 12:00 in +02:00 equals 10:00 UTC.
        Assert.Equal(new DateTimeOffset(2026, 4, 21, 12, 0, 0, TimeSpan.FromHours(2)), parsed.When);
    }

    [Fact]
    public void Naive_timestamp_is_interpreted_as_utc() {
        const string json = "{\"when\":\"2026-04-21T10:00:00\"}";
        var parsed = JsonSerializer.Deserialize<Wrapper>(json, SchemaJsonOptions.Default)!;
        Assert.Equal(TimeSpan.Zero, parsed.When.Offset);
    }
}
