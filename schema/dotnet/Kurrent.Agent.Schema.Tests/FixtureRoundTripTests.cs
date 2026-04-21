using System.Text.Json;
using System.Text.Json.Nodes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

/// <summary>
/// Drift-detection tests. For every fixture under <c>schema/fixtures/events/</c>
/// load the JSON, deserialise into the canonical record, reserialise, and
/// assert structural equality with the original. The Python package runs
/// equivalent tests against the same fixtures.
/// </summary>
public class FixtureRoundTripTests {
    static readonly string FixturesRoot = LocateFixturesRoot();

    public static IEnumerable<object[]> EventFixtureCases() =>
        EventTypeMap.All.Select(pair => new object[] { pair.Value, pair.Key });

    [Theory]
    [MemberData(nameof(EventFixtureCases))]
    public void Event_fixture_round_trips(string eventTypeName, Type clrType) {
        var fixturePath = Path.Combine(FixturesRoot, "events", $"{eventTypeName}.json");
        Assert.True(File.Exists(fixturePath), $"Missing fixture for '{eventTypeName}' at {fixturePath}");

        var originalJson = File.ReadAllText(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        var parsed = JsonSerializer.Deserialize(originalJson, clrType, SchemaJsonOptions.Default);
        Assert.NotNull(parsed);

        var roundTripJson = JsonSerializer.Serialize(parsed, clrType, SchemaJsonOptions.Default);
        var roundTripNode = JsonNode.Parse(roundTripJson)!;

        AssertStructurallyEqual(originalNode, roundTripNode, eventTypeName);
    }

    [Fact]
    public void Usage_metadata_round_trips() {
        var fixturePath = Path.Combine(FixturesRoot, "metadata", "usage.json");
        var originalJson = File.ReadAllText(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        var parsed = JsonSerializer.Deserialize<TokenUsage>(originalJson, SchemaJsonOptions.Default);
        Assert.NotNull(parsed);

        var roundTripJson = JsonSerializer.Serialize(parsed, SchemaJsonOptions.Default);
        var roundTripNode = JsonNode.Parse(roundTripJson)!;

        AssertStructurallyEqual(originalNode, roundTripNode, "usage");
    }

    static void AssertStructurallyEqual(JsonNode expected, JsonNode actual, string context) {
        var expectedCanonical = CanonicaliseJson(expected);
        var actualCanonical   = CanonicaliseJson(actual);
        Assert.True(
            expectedCanonical == actualCanonical,
            $"Round-trip drift for {context}:\nExpected:\n{expectedCanonical}\nActual:\n{actualCanonical}"
        );
    }

    static string CanonicaliseJson(JsonNode node) {
        var opts = new JsonSerializerOptions { WriteIndented = false };
        return Canonicalise(node)?.ToJsonString(opts) ?? "null";
    }

    static JsonNode? Canonicalise(JsonNode? node) => node switch {
        null            => null,
        JsonObject obj  => new JsonObject(obj.OrderBy(kv => kv.Key, StringComparer.Ordinal)
                                             .Select(kv => KeyValuePair.Create(kv.Key, Canonicalise(kv.Value)))),
        JsonArray arr   => new JsonArray(arr.Select(Canonicalise).ToArray()),
        JsonValue val   => JsonNode.Parse(val.ToJsonString()),
        _               => node.DeepClone()
    };

    static string LocateFixturesRoot() {
        // Walk up from the test assembly location until we find schema/fixtures.
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null) {
            var candidate = Path.Combine(dir.FullName, "schema", "fixtures");
            if (Directory.Exists(candidate)) return candidate;

            candidate = Path.Combine(dir.FullName, "fixtures");
            if (Directory.Exists(candidate)
                && File.Exists(Path.Combine(dir.FullName, "SCHEMA_v2.md"))) return candidate;

            dir = dir.Parent;
        }
        throw new DirectoryNotFoundException("Could not locate schema/fixtures relative to test assembly.");
    }
}
