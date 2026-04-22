using System.Text.Json;
using System.Text.Json.Nodes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Drift-detection tests for the MAF .NET write + read path. For every canonical
/// fixture under <c>schema/fixtures/events/</c>:
///
///   1. Parse into the canonical record via the shared <see cref="SchemaJsonOptions.Default"/>.
///   2. <see cref="EventSerializer.Serialize"/> the record (MAF .NET write path).
///   3. Append to KurrentDB.
///   4. Read the stream back, <see cref="EventSerializer.Deserialize"/> to a record.
///   5. Re-serialise the round-tripped record and assert structural equality with the original fixture.
///
/// This pins both:
///   - Byte-equivalent JSON parity with the fixture the Python mirror also round-trips (DEV-1546).
///     If MAF .NET ever drifts from the fixture, this test fails.
///   - The MAF .NET <c>EventSerializer</c> preserves the wire format end-to-end.
/// </summary>
[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class FixtureRoundTripTests(KurrentDbFixture db) {
    static readonly string FixturesRoot = LocateFixturesRoot();

    public static IEnumerable<(string TypeName, Type ClrType)> EventFixtureCases() =>
        EventTypeMap.All.Select(pair => (pair.Value, pair.Key));

    [Test]
    [MethodDataSource(nameof(EventFixtureCases))]
    public async Task Event_fixture_round_trips_through_MAF_write_and_read(string eventTypeName, Type clrType) {
        var fixturePath = Path.Combine(FixturesRoot, "events", $"{eventTypeName}.json");
        await Assert.That(File.Exists(fixturePath)).IsTrue();

        var originalJson = await File.ReadAllTextAsync(fixturePath);
        var originalNode = JsonNode.Parse(originalJson)!;

        // 1) Parse fixture into the canonical record.
        var parsed = JsonSerializer.Deserialize(originalJson, clrType, SchemaJsonOptions.Default);
        await Assert.That(parsed).IsNotNull();

        // 2) Serialise via the MAF .NET write path and assert byte-equivalent JSON with the fixture.
        //    (Parity with the MAF Python mirror that round-trips against the same fixtures.)
        var ed           = EventSerializer.Serialize(parsed!);
        var writtenNode  = JsonNode.Parse(ed.Data.Span)!;
        AssertStructurallyEqual(originalNode, writtenNode, $"{eventTypeName} (Serialize)");

        // 3) Append to KurrentDB and 4) read it back via the MAF .NET read path.
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [ed]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        var decoded = EventSerializer.Deserialize(read);
        await Assert.That(decoded).IsNotNull();

        // 5) Re-serialise the decoded record and assert the full write→read→write cycle matches the fixture.
        var roundTripped = JsonSerializer.Serialize(decoded, clrType, SchemaJsonOptions.Default);
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
        JsonValue val   => JsonNode.Parse(val.ToJsonString()),
        _               => node.DeepClone()
    };

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
