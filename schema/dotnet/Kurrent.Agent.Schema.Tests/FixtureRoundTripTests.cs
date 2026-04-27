using System.Text.Json;
using System.Text.Json.Nodes;
using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Xunit;

namespace Kurrent.Agent.Schema.Tests;

public class FixtureRoundTripTests {
    static readonly string FixturesRoot = LocateFixturesRoot();

    public static IEnumerable<object[]> EventFixtureCases() =>
        EventTypeMap.All.Select(pair => new object[] { pair.Key, pair.Value });

    [Theory]
    [MemberData(nameof(EventFixtureCases))]
    public void Event_fixture_round_trips(string eventTypeName, Type clrType) {
        var fixturePath = Path.Combine(FixturesRoot, "events", $"{eventTypeName}.json");
        Assert.True(File.Exists(fixturePath), $"Missing fixture for '{eventTypeName}' at {fixturePath}");

        var originalJson = File.ReadAllText(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        var parseMethod  = typeof(SchemaJsonOptions).GetMethod(nameof(SchemaJsonOptions.FromJson))!
                            .MakeGenericMethod(clrType);
        var parsed = (IMessage)parseMethod.Invoke(null, new object[] { originalJson })!;
        var roundTripJson = SchemaJsonOptions.ToJson(parsed);
        var roundTripNode = JsonNode.Parse(roundTripJson)!;

        AssertStructurallyEqual(originalNode, roundTripNode, eventTypeName);
    }

    [Fact]
    public void Usage_metadata_round_trips() {
        var fixturePath = Path.Combine(FixturesRoot, "metadata", "usage.json");
        var originalJson = File.ReadAllText(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        var parsed = SchemaJsonOptions.FromJson<TokenUsage>(originalJson);
        var roundTripJson = SchemaJsonOptions.ToJson(parsed);
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
        JsonValue val   => CanonicaliseValue(val),
        _               => node.DeepClone()
    };

    // Cross-language number parity: proto3 JSON canonical form lets
    // language implementations differ on whether a `Struct`/`Value`
    // float-valued integer is rendered as `5000.0` (Python) or `5000`
    // (.NET). Both are valid; for fixture-drift comparison we
    // normalise to a single textual form by parsing as `double` and
    // re-emitting integer-valued doubles without trailing `.0`.
    static JsonNode CanonicaliseValue(JsonValue val) {
        var element = val.GetValue<JsonElement>();
        if (element.ValueKind == JsonValueKind.Number) {
            if (element.TryGetInt64(out var i64)) return JsonValue.Create(i64);
            if (element.TryGetDouble(out var d)) {
                if (!double.IsNaN(d) && !double.IsInfinity(d) && d == Math.Truncate(d)
                    && d >= long.MinValue && d <= long.MaxValue) {
                    return JsonValue.Create((long)d);
                }
                return JsonValue.Create(d);
            }
        }
        return JsonNode.Parse(val.ToJsonString())!;
    }

    static string LocateFixturesRoot() {
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
