using System.Text.Json;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Exercises the full Serialize → append to KurrentDB → read → Deserialize path.
/// Each test writes one event type and asserts it round-trips identically.
/// </summary>
[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class SerializationRoundTripTests(KurrentDbFixture db) {
    static readonly DateTimeOffset Ts = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);

    async Task<object?> RoundTrip(object @event) {
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        await client.AppendToStreamAsync(
            streamName, StreamState.NoStream, [EventSerializer.Serialize(@event)]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        return EventSerializer.Deserialize(read);
    }

    [Test]
    public async Task SessionStarted_RoundTrips() {
        var original = new SessionStarted(
            AppName:           "demo-app",
            AgentName:         "agent-x",
            Model:             "claude-test",
            TenantId:          "tenant-1",
            UserId:            "user-1",
            AgentConfig:       null,
            PreviousSessionId: "sess-prior",
            Timestamp:         Ts);

        var decoded = await RoundTrip(original);

        await Assert.That(decoded).IsEqualTo(original);
    }

    [Test]
    public async Task SessionEnded_RoundTrips() {
        var original = new SessionEnded("completed", Ts);

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task UserMessageReceived_RoundTrips() {
        var original = new UserMessageReceived("hello", "m-1", "daisy", Ts, 0, Ts);

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task AssistantTextGenerated_RoundTrips() {
        var original = new AssistantTextGenerated("hi there", "m-2", "agent-x", Ts, 1, Ts);

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task AssistantToolCallsGenerated_RoundTrips() {
        var args     = JsonSerializer.SerializeToElement(new Dictionary<string, object?> { ["city"] = "Paris" });
        var original = new AssistantToolCallsGenerated(
            ToolCalls: [new ToolCallInfo("call-1", "get_weather", args)],
            Content: "looking",
            MessageId: "m-3",
            AuthorName: "agent-x",
            CreatedAt: Ts,
            MessageIndex: 2,
            Timestamp: Ts
        );

        var decoded = await RoundTrip(original) as AssistantToolCallsGenerated;

        await Assert.That(decoded).IsNotNull();
        await Assert.That(decoded!.Content).IsEqualTo("looking");
        await Assert.That(decoded.MessageIndex).IsEqualTo(2);
        await Assert.That(decoded.ToolCalls.Count).IsEqualTo(1);
        await Assert.That(decoded.ToolCalls[0].CallId).IsEqualTo("call-1");
        await Assert.That(decoded.ToolCalls[0].ToolName).IsEqualTo("get_weather");
        // JsonElement comparison: compare the raw JSON text, which survives the round-trip.
        await Assert.That(decoded.ToolCalls[0].Arguments!.Value.GetRawText())
            .IsEqualTo(args.GetRawText());
    }

    [Test]
    public async Task ToolResultReceived_RoundTrips() {
        var original = new ToolResultReceived("call-1", "get_weather", "sunny", "m-4", "agent-x", Ts, 3, Ts);

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task EvalEvents_RoundTrip() {
        var started   = new EvalRunStarted("sess-1", "heuristic", "correctness", Ts);
        var scored    = new TurnScored("sess-1", 0, "in", "out", 0.85, "good", "solid answer", Ts);
        var completed = new EvalRunCompleted("sess-1", 3, 0.9, 0.012, Ts);

        await Assert.That(await RoundTrip(started)).IsEqualTo(started);
        await Assert.That(await RoundTrip(scored)).IsEqualTo(scored);
        await Assert.That(await RoundTrip(completed)).IsEqualTo(completed);
    }

    [Test]
    public async Task UnknownEventType_DeserializesAsNull() {
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        // Write a bare EventData with a type name that isn't registered in EventTypeMap.
        var raw = new EventData(Uuid.NewUuid(), "NotARegisteredEventType", "{}"u8.ToArray());
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [raw]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        await Assert.That(EventSerializer.Deserialize(read)).IsNull();
    }

    [Test]
    public async Task Serialize_StoresMetadataWhenProvided() {
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var metadata = new Dictionary<string, object?> { ["tenant_id"] = "t-1", ["trace_id"] = "abc" };
        var ed       = EventSerializer.Serialize(
            new SessionStarted(
                AppName:           null,
                AgentName:         "a",
                Model:             "m",
                TenantId:          null,
                UserId:            null,
                AgentConfig:       null,
                PreviousSessionId: null,
                Timestamp:         Ts),
            metadata: metadata);

        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [ed]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        var meta = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(read.Event.Metadata.Span);
        await Assert.That(meta).IsNotNull();
        await Assert.That(meta!["tenant_id"].GetString()).IsEqualTo("t-1");
        await Assert.That(meta["trace_id"].GetString()).IsEqualTo("abc");
        // SCHEMA_v2 §9: writers stamp $schema_version on every canonical event.
        await Assert.That(meta["$schema_version"].GetInt32()).IsEqualTo(SchemaVersion.Current);
    }

    [Test]
    public async Task Serialize_StampsSchemaVersionEvenWithoutCallerMetadata() {
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var ed = EventSerializer.Serialize(new SessionEnded("done", Ts));
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [ed]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        var meta = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(read.Event.Metadata.Span);
        await Assert.That(meta).IsNotNull();
        await Assert.That(meta!["$schema_version"].GetInt32()).IsEqualTo(SchemaVersion.Current);
    }

    [Test]
    public async Task Serialize_SchemaVersionCannotBeOverriddenByCallerMetadata() {
        // SCHEMA_v2 §9 requires the writer's schema version to be authoritative.
        // The serializer must stamp $schema_version last, winning over any caller value.
        using var client     = db.CreateClient();
        var       streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var rogueMetadata = new Dictionary<string, object?> {
            ["$schema_version"] = 99,
            ["tenant_id"]       = "t-1",
        };
        var ed = EventSerializer.Serialize(new SessionEnded("done", Ts), metadata: rogueMetadata);
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [ed]);

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        var meta = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(read.Event.Metadata.Span);
        await Assert.That(meta).IsNotNull();
        await Assert.That(meta!["$schema_version"].GetInt32()).IsEqualTo(SchemaVersion.Current);
        // Other caller-supplied keys still pass through untouched.
        await Assert.That(meta["tenant_id"].GetString()).IsEqualTo("t-1");
    }
}
