using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.ChatHistory;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Covers the directly-testable surface of <see cref="KurrentDBChatHistoryProvider"/>.
/// The Provide/Store overrides require full Microsoft.Agents.AI orchestration
/// (InvokingContext/InvokedContext with AIAgent + AgentSession) which is covered
/// by the downstream Microsoft.Agents.AI framework; here we only pin the inputs
/// and outputs we own directly.
/// </summary>
[ClassDataSource<KurrentDbFixture>(Shared = SharedType.PerTestSession)]
public class KurrentDBChatHistoryProviderTests(KurrentDbFixture db) {
    [Test]
    public async Task EndSessionAsync_AppendsSessionEndedToSessionStream() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var provider     = new KurrentDBChatHistoryProvider(client, sessionId);

        await provider.EndSessionAsync("completed");

        var read = await client
            .ReadStreamAsync(Direction.Forwards, StreamNames.AgentSession(sessionId), StreamPosition.Start)
            .SingleAsync();

        await Assert.That(read.Event.EventType).IsEqualTo("SessionEnded");
        var ended = EventSerializer.Deserialize(read) as SessionEnded;
        await Assert.That(ended).IsNotNull();
        await Assert.That(ended!.Reason).IsEqualTo("completed");
    }

    [Test]
    public async Task EndSessionAsync_WithNullReason_StillAppends() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var provider     = new KurrentDBChatHistoryProvider(client, sessionId);

        await provider.EndSessionAsync();

        var read = await client
            .ReadStreamAsync(Direction.Forwards, StreamNames.AgentSession(sessionId), StreamPosition.Start)
            .SingleAsync();

        var ended = EventSerializer.Deserialize(read) as SessionEnded;
        await Assert.That(ended).IsNotNull();
        // proto3 optional: HasReason tracks presence; Reason returns "" when unset.
        await Assert.That(ended!.HasReason).IsFalse();
    }

    // --- message_index continuation (qodo review, DEV-1548) ---

    static readonly DateTimeOffset IndexTs  = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);
    static readonly Timestamp      IndexPts = Timestamp.FromDateTimeOffset(IndexTs);

    static UserMessageReceived UserMsg(string content, string messageId, int idx) =>
        new() { Content = content, MessageId = messageId, AuthorName = "user", CreatedAt = IndexPts, MessageIndex = idx, Timestamp = IndexPts };

    static AssistantTextGenerated AsstText(string content, string messageId, int idx) =>
        new() { Content = content, MessageId = messageId, AuthorName = "agent", CreatedAt = IndexPts, MessageIndex = idx, Timestamp = IndexPts };

    [Test]
    public async Task ReadNextMessageIndex_OnMissingStream_ReturnsZero() {
        using var client = db.CreateClient();

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(
            client, Guid.NewGuid().ToString("N"));

        await Assert.That(next).IsEqualTo(0);
    }

    [Test]
    public async Task ReadNextMessageIndex_ReturnsHighestChatIndexPlusOne() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [
            EventSerializer.Serialize(UserMsg("q", "m-1", 0)),
            EventSerializer.Serialize(AsstText("a", "m-2", 1)),
            EventSerializer.Serialize(UserMsg("q2", "m-3", 2)),
        ]);

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(client, sessionId);

        await Assert.That(next).IsEqualTo(3);
    }

    [Test]
    public async Task ReadNextMessageIndex_IgnoresNonChatEvents() {
        // SessionStarted / SessionEnded have no message_index; they must not reset the counter.
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);

        await client.AppendToStreamAsync(streamName, StreamState.NoStream, [
            EventSerializer.Serialize(new SessionStarted { AgentName = "a", Model = "m", Timestamp = IndexPts }),
            EventSerializer.Serialize(UserMsg("hi", "m-1", 5)),
            EventSerializer.Serialize(new SessionEnded { Reason = "done", Timestamp = IndexPts }),
        ]);

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(client, sessionId);

        await Assert.That(next).IsEqualTo(6);
    }

    [Test]
    public async Task ApprovalRequestRoundTripsThroughKurrentDB() {
        using var client = db.CreateClient();
        var sessionId    = Guid.NewGuid().ToString("N");
        var streamName   = StreamNames.AgentSession(sessionId);
        var now          = DateTimeOffset.UtcNow;

        var fc       = new FunctionCallContent("call-1", "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" });
        var approval = new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc);
        var carrier  = new ChatMessage(ChatRole.Assistant, [
            new TextContent("Drafting…"),
            approval,
        ]) { MessageId = "asst-msg-1" };

        var events = ChatMessageConverter.ToEvents(carrier, messageIndex: 0, timestamp: now)
            .Select(e => EventSerializer.Serialize(e))
            .ToArray();
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, events);

        // Read back via the converter path the provider uses internally — same grouping, same merge.
        var resolved = new List<object>();
        await foreach (var re in client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start)) {
            if (EventSerializer.Deserialize(re) is { } e) resolved.Add(e);
        }

        var issued   = resolved.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);
        var rebuilt  = ChatMessageConverter.MergeIntoChatMessage(resolved, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(rebuilt.MessageId).IsEqualTo("asst-msg-1");
        await Assert.That(rebuilt.Contents.OfType<TextContent>().Single().Text).IsEqualTo("Drafting…");
        await Assert.That(((FunctionCallContent)rebuilt.Contents.OfType<ToolApprovalRequestContent>().Single()
            .ToolCall).Name).IsEqualTo("send_email");
    }
}
