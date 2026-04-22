using System.Text.Json;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class SessionTurnReaderTests(KurrentDbFixture db) {
    static readonly DateTimeOffset Ts = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);

    static async Task AppendAsync(KurrentDBClient client, string streamName, params EventData[] events) {
        await client.AppendToStreamAsync(streamName, StreamState.Any, events);
    }

    static EventData EventFor(object @event, IDictionary<string, object?>? metadata = null) =>
        EventSerializer.Serialize(@event, metadata: metadata);

    [Test]
    public async Task ReadTurns_OnMissingStream_ReturnsEmpty() {
        using var client = db.CreateClient();

        var turns = await SessionTurnReader.ReadTurnsAsync(client, Guid.NewGuid().ToString("N"));

        await Assert.That(turns).IsEmpty();
    }

    [Test]
    public async Task ReadTurns_SingleUserAssistantPair_YieldsOneTurn() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        await AppendAsync(client, streamName,
            EventFor(new SessionStarted(
                AppName:           null,
                AgentName:         "agent",
                Model:             "model",
                TenantId:          null,
                UserId:            null,
                AgentConfig:       null,
                PreviousSessionId: null,
                Timestamp:         Ts)),
            EventFor(new UserMessageReceived("hello", "m-1", "user", Ts, 0, Ts)),
            EventFor(new AssistantTextGenerated("hi back", "m-2", "agent", Ts, 1, Ts)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].Index).IsEqualTo(0);
        await Assert.That(turns[0].UserInput).IsEqualTo("hello");
        await Assert.That(turns[0].AssistantOutput).IsEqualTo("hi back");
        await Assert.That(turns[0].ToolCalls).IsEmpty();
    }

    [Test]
    public async Task ReadTurns_MultipleTurns_AreSegmentedOnUserMessage() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        await AppendAsync(client, streamName,
            EventFor(new UserMessageReceived("Q1", "m-1", "user", Ts, 0, Ts)),
            EventFor(new AssistantTextGenerated("A1", "m-2", "agent", Ts, 1, Ts)),
            EventFor(new UserMessageReceived("Q2", "m-3", "user", Ts, 2, Ts)),
            EventFor(new AssistantTextGenerated("A2", "m-4", "agent", Ts, 3, Ts)),
            EventFor(new UserMessageReceived("Q3", "m-5", "user", Ts, 4, Ts)),
            EventFor(new AssistantTextGenerated("A3", "m-6", "agent", Ts, 5, Ts)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(3);
        await Assert.That(turns.Select(t => t.UserInput!).ToList()).IsEquivalentTo(new[] { "Q1", "Q2", "Q3" });
        await Assert.That(turns.Select(t => t.AssistantOutput!).ToList()).IsEquivalentTo(new[] { "A1", "A2", "A3" });
        await Assert.That(turns.Select(t => t.Index)).IsEquivalentTo(new[] { 0, 1, 2 });
    }

    [Test]
    public async Task ReadTurns_ToolCallAndResult_AreCorrelated() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        var args = JsonSerializer.SerializeToElement(new Dictionary<string, object?> { ["city"] = "Paris" });

        await AppendAsync(client, streamName,
            EventFor(new UserMessageReceived("weather?", "m-1", "user", Ts, 0, Ts)),
            EventFor(new AssistantToolCallsGenerated(
                ToolCalls: [new ToolCallInfo("call-1", "get_weather", args)],
                Content: null,
                MessageId: "m-2",
                AuthorName: "agent",
                CreatedAt: Ts,
                MessageIndex: 1,
                Timestamp: Ts)),
            EventFor(new ToolResultReceived("call-1", "get_weather", "sunny", "m-3", null, Ts, 2, Ts)),
            EventFor(new AssistantTextGenerated("it's sunny", "m-4", "agent", Ts, 3, Ts)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].ToolCalls.Count).IsEqualTo(1);
        var tc = turns[0].ToolCalls[0];
        await Assert.That(tc.Name).IsEqualTo("get_weather");
        await Assert.That(tc.Result).IsEqualTo("sunny");
    }

    [Test]
    public async Task ReadTurns_AggregatesUsageFromMetadata() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        static Dictionary<string, object?> Usage(long input, long output) => new() {
            ["$usage"] = new Dictionary<string, object?> {
                ["input_tokens"]  = input,
                ["output_tokens"] = output,
            },
        };

        await AppendAsync(client, streamName,
            EventFor(new UserMessageReceived("hi", "m-1", "user", Ts, 0, Ts)),
            EventFor(new AssistantToolCallsGenerated(
                    ToolCalls: [new ToolCallInfo("call-1", "t", null)],
                    Content: null,
                    MessageId: "m-2",
                    AuthorName: "agent",
                    CreatedAt: Ts,
                    MessageIndex: 1,
                    Timestamp: Ts),
                metadata: Usage(10, 5)),
            EventFor(new ToolResultReceived("call-1", "t", "r", "m-3", null, Ts, 2, Ts)),
            EventFor(new AssistantTextGenerated("done", "m-4", "agent", Ts, 3, Ts),
                metadata: Usage(20, 7)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].InputTokens).IsEqualTo(30L);
        await Assert.That(turns[0].OutputTokens).IsEqualTo(12L);
    }

    [Test]
    public async Task ReadTurns_UserMessageWithoutAssistantReply_StillProducesTurn() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        // Mid-flight session: user asked, agent hasn't responded yet.
        await AppendAsync(client, streamName,
            EventFor(new UserMessageReceived("pending", "m-1", "user", Ts, 0, Ts)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].UserInput).IsEqualTo("pending");
        await Assert.That(turns[0].AssistantOutput).IsNull();
    }

    [Test]
    public async Task ReadTurns_UnknownEventTypes_AreSkipped() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        // An event whose type isn't in EventTypeMap should not derail the reader.
        var raw = new EventData(Uuid.NewUuid(), "TotallyUnknown", "{}"u8.ToArray());

        await AppendAsync(client, streamName,
            EventFor(new UserMessageReceived("Q", "m-1", "user", Ts, 0, Ts)),
            raw,
            EventFor(new AssistantTextGenerated("A", "m-2", "agent", Ts, 1, Ts)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].UserInput).IsEqualTo("Q");
        await Assert.That(turns[0].AssistantOutput).IsEqualTo("A");
    }
}
