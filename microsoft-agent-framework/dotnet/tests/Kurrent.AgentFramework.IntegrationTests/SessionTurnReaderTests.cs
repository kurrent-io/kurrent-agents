using System.Text.Json;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Eval;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class SessionTurnReaderTests(KurrentDbFixture db) {
    static readonly DateTimeOffset Ts  = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      Pts = Timestamp.FromDateTimeOffset(Ts);

    static async Task AppendAsync(KurrentDBClient client, string streamName, params EventData[] events) {
        await client.AppendToStreamAsync(streamName, StreamState.Any, events);
    }

    static EventData EventFor(object @event, IDictionary<string, object?>? metadata = null) =>
        EventSerializer.Serialize(@event, metadata: metadata);

    static UserMessageReceived UserMsg(string content, string messageId, int idx) =>
        new() { Content = content, MessageId = messageId, AuthorName = "user", CreatedAt = Pts, MessageIndex = idx, Timestamp = Pts };

    static AssistantTextGenerated AsstText(string content, string messageId, int idx) =>
        new() { Content = content, MessageId = messageId, AuthorName = "agent", CreatedAt = Pts, MessageIndex = idx, Timestamp = Pts };

    [Test]
    public async Task ReadTurns_OnMissingStream_ReturnsEmpty() {
        await using var client = db.CreateClient();

        var turns = await SessionTurnReader.ReadTurnsAsync(client, Guid.NewGuid().ToString("N"));

        await Assert.That(turns).IsEmpty();
    }

    [Test]
    public async Task ReadTurns_SingleUserAssistantPair_YieldsOneTurn() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        await AppendAsync(
            client,
            streamName,
            EventFor(new SessionStarted { AgentName = "agent", Model = "model", Timestamp = Pts }),
            EventFor(UserMsg("hello", "m-1", 0)),
            EventFor(AsstText("hi back", "m-2", 1))
        );

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].Index).IsEqualTo(0);
        await Assert.That(turns[0].UserInput).IsEqualTo("hello");
        await Assert.That(turns[0].AssistantOutput).IsEqualTo("hi back");
        await Assert.That(turns[0].ToolCalls).IsEmpty();
    }

    [Test]
    public async Task ReadTurns_MultipleTurns_AreSegmentedOnUserMessage() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        await AppendAsync(
            client,
            streamName,
            EventFor(UserMsg("Q1", "m-1", 0)),
            EventFor(AsstText("A1", "m-2", 1)),
            EventFor(UserMsg("Q2", "m-3", 2)),
            EventFor(AsstText("A2", "m-4", 3)),
            EventFor(UserMsg("Q3", "m-5", 4)),
            EventFor(AsstText("A3", "m-6", 5))
        );

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(3);
        await Assert.That(turns.Select(t => t.UserInput!).ToList()).IsEquivalentTo(new[] { "Q1", "Q2", "Q3" });
        await Assert.That(turns.Select(t => t.AssistantOutput!).ToList()).IsEquivalentTo(new[] { "A1", "A2", "A3" });
        await Assert.That(turns.Select(t => t.Index)).IsEquivalentTo(new[] { 0, 1, 2 });
    }

    [Test]
    public async Task ReadTurns_ToolCallAndResult_AreCorrelated() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        var args = ChatMessageConverter.JsonElementToStruct(
            JsonSerializer.SerializeToElement(new Dictionary<string, object?> { ["city"] = "Paris" })
        );

        var toolCalls = new AssistantToolCallsGenerated {
            MessageId    = "m-2",
            AuthorName   = "agent",
            CreatedAt    = Pts,
            MessageIndex = 1,
            Timestamp    = Pts,
        };
        toolCalls.ToolCalls.Add(new ToolCallInfo { CallId = "call-1", ToolName = "get_weather", Arguments = args });

        await AppendAsync(
            client,
            streamName,
            EventFor(UserMsg("weather?", "m-1", 0)),
            EventFor(toolCalls),
            EventFor(
                new ToolResultReceived {
                    CallId       = "call-1",
                    ToolName     = "get_weather",
                    Result       = "sunny",
                    MessageId    = "m-3",
                    CreatedAt    = Pts,
                    MessageIndex = 2,
                    Timestamp    = Pts,
                }
            ),
            EventFor(AsstText("it's sunny", "m-4", 3))
        );

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].ToolCalls.Count).IsEqualTo(1);
        var tc = turns[0].ToolCalls[0];
        await Assert.That(tc.Name).IsEqualTo("get_weather");
        await Assert.That(tc.Result).IsEqualTo("sunny");
    }

    [Test]
    public async Task ReadTurns_AggregatesUsageFromMetadata() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        var toolCalls = new AssistantToolCallsGenerated {
            MessageId    = "m-2",
            AuthorName   = "agent",
            CreatedAt    = Pts,
            MessageIndex = 1,
            Timestamp    = Pts,
        };
        toolCalls.ToolCalls.Add(new ToolCallInfo { CallId = "call-1", ToolName = "t" });

        await AppendAsync(
            client,
            streamName,
            EventFor(UserMsg("hi", "m-1", 0)),
            EventFor(toolCalls, metadata: Usage(10, 5)),
            EventFor(
                new ToolResultReceived {
                    CallId    = "call-1", ToolName = "t", Result       = "r",
                    MessageId = "m-3", CreatedAt   = Pts, MessageIndex = 2, Timestamp = Pts,
                }
            ),
            EventFor(AsstText("done", "m-4", 3), metadata: Usage(20, 7))
        );

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].InputTokens).IsEqualTo(30L);
        await Assert.That(turns[0].OutputTokens).IsEqualTo(12L);

        return;

        static Dictionary<string, object?> Usage(long input, long output) => new() {
            ["$usage"] = new Dictionary<string, object?> {
                ["input_tokens"]  = input,
                ["output_tokens"] = output,
            },
        };
    }

    [Test]
    public async Task ReadTurns_UserMessageWithoutAssistantReply_StillProducesTurn() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        // Mid-flight session: user asked, agent hasn't responded yet.
        await AppendAsync(client, streamName, EventFor(UserMsg("pending", "m-1", 0)));

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].UserInput).IsEqualTo("pending");
        await Assert.That(turns[0].AssistantOutput).IsNull();
    }

    [Test]
    public async Task ReadTurns_UnknownEventTypes_AreSkipped() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        // An event whose type isn't in EventTypeMap should not derail the reader.
        var raw = new EventData(Uuid.NewUuid(), "TotallyUnknown", "{}"u8.ToArray());

        await AppendAsync(
            client,
            streamName,
            EventFor(UserMsg("Q", "m-1", 0)),
            raw,
            EventFor(AsstText("A", "m-2", 1))
        );

        var turns = await SessionTurnReader.ReadTurnsAsync(client, sessionId);

        await Assert.That(turns.Count).IsEqualTo(1);
        await Assert.That(turns[0].UserInput).IsEqualTo("Q");
        await Assert.That(turns[0].AssistantOutput).IsEqualTo("A");
    }
}
