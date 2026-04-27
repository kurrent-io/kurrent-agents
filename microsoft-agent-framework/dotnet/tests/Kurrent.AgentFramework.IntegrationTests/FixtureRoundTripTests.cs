using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Google.Protobuf;
using Kurrent.Agent.Schema;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Drift-detection tests for the MAF .NET write + read path. For every canonical
/// fixture under <c>schema/fixtures/events/</c>:
///
///   1. Parse into the canonical message via the shared <see cref="SchemaJsonOptions"/>.
///   2. <see cref="EventSerializer.Serialize"/> the message (MAF .NET write path).
///   3. Append to KurrentDB.
///   4. Read the stream back, <see cref="EventSerializer.Deserialize"/> to a message.
///   5. Re-serialise the round-tripped message and assert structural equality with the original fixture.
///
/// This pins both:
///   - Structural JSON parity with the fixture the Python mirror also round-trips (DEV-1546).
///     Equality is computed after canonicalising both sides (key sort) so property-order
///     differences don't cause false positives — raw UTF-8 is not guaranteed to match
///     byte-for-byte across the two runtimes.
///   - The MAF .NET <c>EventSerializer</c> preserves the event payload structure end-to-end.
/// </summary>
[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class FixtureRoundTripTests(KurrentDbFixture db) {
    static readonly string FixturesRoot = LocateFixturesRoot();

    static readonly MethodInfo FromJsonGeneric =
        typeof(SchemaJsonOptions).GetMethod(nameof(SchemaJsonOptions.FromJson))!;

    public static IEnumerable<(string TypeName, Type ClrType)> EventFixtureCases() =>
        EventTypeMap.All.Select(pair => (pair.Key, pair.Value));

    [Test]
    [MethodDataSource(nameof(EventFixtureCases))]
    public async Task Event_fixture_round_trips_through_MAF_write_and_read(string eventTypeName, Type clrType) {
        var fixturePath = Path.Combine(FixturesRoot, "events", $"{eventTypeName}.json");
        await Assert.That(File.Exists(fixturePath)).IsTrue();

        var originalJson = await File.ReadAllTextAsync(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        // 1) Parse fixture into the canonical message.
        var parsed = (IMessage)FromJsonGeneric.MakeGenericMethod(clrType).Invoke(null, [originalJson])!;

        // 2) Serialise via the MAF .NET write path and assert structural JSON parity with the fixture.
        var ed           = EventSerializer.Serialize(parsed);
        var writtenNode  = JsonNode.Parse(ed.Data.Span)!;
        AssertStructurallyEqual(originalNode, writtenNode, $"{eventTypeName} (Serialize)");

        // 3) Append to KurrentDB and 4) read it back via the MAF .NET read path.
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [ed]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        var decoded = (IMessage?)EventSerializer.Deserialize(read);
        await Assert.That(decoded).IsNotNull();

        // 5) Re-serialise the decoded message and assert the full write→read→write cycle matches the fixture.
        var roundTripped  = SchemaJsonOptions.ToJson(decoded!);
        var roundTripNode = JsonNode.Parse(roundTripped)!;
        AssertStructurallyEqual(originalNode, roundTripNode, $"{eventTypeName} (read-back)");

        // Metadata stamping — SCHEMA_v2 §9.
        var meta = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(read.Event.Metadata.Span)!;
        await Assert.That(meta["$schema_version"].GetInt32()).IsEqualTo(SchemaVersion.Current);
    }

    static void AssertStructurallyEqual(JsonNode expected, JsonNode actual, string context) {
        var expectedCanonical = CanonicaliseJson(expected);
        var actualCanonical   = CanonicaliseJson(actual);
        if (expectedCanonical != actualCanonical) {
            throw new InvalidOperationException(
                $"Fixture drift for {context}:\nExpected:\n{expectedCanonical}\nActual:\n{actualCanonical}");
        }
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

    // Cross-language number parity: proto3 JSON canonical form lets language
    // implementations differ on whether a Struct/Value float-valued integer is
    // rendered as 5000.0 (Python) or 5000 (.NET). Both are valid; normalise to
    // a single textual form by collapsing integer-valued doubles to long.
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
            dir = dir.Parent;
        }
        throw new DirectoryNotFoundException("Could not locate schema/fixtures relative to test assembly.");
    }
}
