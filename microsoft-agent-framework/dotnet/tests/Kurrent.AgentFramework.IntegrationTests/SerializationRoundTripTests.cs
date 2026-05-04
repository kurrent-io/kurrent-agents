using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
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
    static readonly DateTimeOffset Ts  = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      Pts = Timestamp.FromDateTimeOffset(Ts);

    async Task<object?> RoundTrip(object @event) {
        await using var client     = db.CreateClient();
        var             streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        await client.AppendToStreamAsync(
            streamName,
            StreamState.NoStream,
            [EventSerializer.Serialize(@event)]
        );

        var read = await client
            .ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start, maxCount: 1)
            .SingleAsync();

        return EventSerializer.Deserialize(read);
    }

    [Test]
    public async Task SessionStarted_RoundTrips() {
        var original = new SessionStarted {
            AppName           = "demo-app",
            AgentName         = "agent-x",
            Model             = "claude-test",
            TenantId          = "tenant-1",
            UserId            = "user-1",
            PreviousSessionId = "sess-prior",
            Timestamp         = Pts,
        };

        var decoded = await RoundTrip(original);

        await Assert.That(decoded).IsEqualTo(original);
    }

    [Test]
    public async Task SessionEnded_RoundTrips() {
        var original = new SessionEnded { Reason = "completed", Timestamp = Pts };

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task UserMessageReceived_RoundTrips() {
        var original = new UserMessageReceived {
            Content      = "hello",
            MessageId    = "m-1",
            AuthorName   = "daisy",
            CreatedAt    = Pts,
            MessageIndex = 0,
            Timestamp    = Pts,
        };

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task AssistantTextGenerated_RoundTrips() {
        var original = new AssistantTextGenerated {
            Content      = "hi there",
            MessageId    = "m-2",
            AuthorName   = "agent-x",
            CreatedAt    = Pts,
            MessageIndex = 1,
            Timestamp    = Pts,
        };

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task AssistantToolCallsGenerated_RoundTrips() {
        var args = ChatMessageConverter.JsonElementToStruct(
            JsonSerializer.SerializeToElement(new Dictionary<string, object?> { ["city"] = "Paris" })
        );

        var original = new AssistantToolCallsGenerated {
            Content      = "looking",
            MessageId    = "m-3",
            AuthorName   = "agent-x",
            CreatedAt    = Pts,
            MessageIndex = 2,
            Timestamp    = Pts,
        };
        original.ToolCalls.Add(new ToolCallInfo { CallId = "call-1", ToolName = "get_weather", Arguments = args });

        var decoded = await RoundTrip(original) as AssistantToolCallsGenerated;

        await Assert.That(decoded).IsNotNull();
        await Assert.That(decoded!.Content).IsEqualTo("looking");
        await Assert.That(decoded.MessageIndex).IsEqualTo(2);
        await Assert.That(decoded.ToolCalls.Count).IsEqualTo(1);
        await Assert.That(decoded.ToolCalls[0].CallId).IsEqualTo("call-1");
        await Assert.That(decoded.ToolCalls[0].ToolName).IsEqualTo("get_weather");
        await Assert.That(decoded.ToolCalls[0].Arguments?.Fields["city"].StringValue).IsEqualTo("Paris");
    }

    [Test]
    public async Task ToolResultReceived_RoundTrips() {
        var original = new ToolResultReceived {
            CallId       = "call-1",
            ToolName     = "get_weather",
            Result       = "sunny",
            MessageId    = "m-4",
            AuthorName   = "agent-x",
            CreatedAt    = Pts,
            MessageIndex = 3,
            Timestamp    = Pts,
        };

        await Assert.That(await RoundTrip(original)).IsEqualTo(original);
    }

    [Test]
    public async Task EvalEvents_RoundTrip() {
        var started   = new EvalRunStarted { SessionId   = "sess-1", Scorer      = "heuristic", Criteria = "correctness", Timestamp = Pts };
        var scored    = new TurnScored { SessionId       = "sess-1", TurnIndex   = 0, Input = "in", Output = "out", Score = 0.85, ScoreLabel = "good", Reason = "solid answer", Timestamp = Pts };
        var completed = new EvalRunCompleted { SessionId = "sess-1", TurnsScored = 3, AverageScore = 0.9, TotalCost = 0.012, Timestamp = Pts };

        await Assert.That(await RoundTrip(started)).IsEqualTo(started);
        await Assert.That(await RoundTrip(scored)).IsEqualTo(scored);
        await Assert.That(await RoundTrip(completed)).IsEqualTo(completed);
    }

    [Test]
    public async Task UnknownEventType_DeserializesAsNull() {
        await using var client     = db.CreateClient();
        var             streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

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
        await using var client     = db.CreateClient();
        var             streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var metadata = new Dictionary<string, object?> { ["tenant_id"] = "t-1", ["trace_id"] = "abc" };

        var ed = EventSerializer.Serialize(
            new SessionStarted { AgentName = "a", Model = "m", Timestamp = Pts },
            metadata: metadata
        );

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
        await using var client     = db.CreateClient();
        var             streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var ed = EventSerializer.Serialize(new SessionEnded { Reason = "done", Timestamp = Pts });
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
        await using var client     = db.CreateClient();
        var             streamName = StreamNames.AgentSession(Guid.NewGuid().ToString("N"));

        var rogueMetadata = new Dictionary<string, object?> {
            ["$schema_version"] = 99,
            ["tenant_id"]       = "t-1",
        };

        var ed = EventSerializer.Serialize(
            new SessionEnded { Reason = "done", Timestamp = Pts },
            metadata: rogueMetadata
        );
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
