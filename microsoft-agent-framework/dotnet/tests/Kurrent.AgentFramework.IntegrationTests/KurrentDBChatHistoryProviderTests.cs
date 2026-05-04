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
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");
        var             provider  = new KurrentDBChatHistoryProvider(client, sessionId);

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
        await using var client    = db.CreateClient();
        var             sessionId = Guid.NewGuid().ToString("N");
        var             provider  = new KurrentDBChatHistoryProvider(client, sessionId);

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
        await using var client = db.CreateClient();

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(
            client,
            Guid.NewGuid().ToString("N")
        );

        await Assert.That(next).IsEqualTo(0);
    }

    [Test]
    public async Task ReadNextMessageIndex_ReturnsHighestChatIndexPlusOne() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        await client.AppendToStreamAsync(
            streamName,
            StreamState.NoStream,
            [
                EventSerializer.Serialize(UserMsg("q", "m-1", 0)),
                EventSerializer.Serialize(AsstText("a", "m-2", 1)),
                EventSerializer.Serialize(UserMsg("q2", "m-3", 2)),
            ]
        );

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(client, sessionId);

        await Assert.That(next).IsEqualTo(3);
    }

    [Test]
    public async Task ReadNextMessageIndex_IgnoresNonChatEvents() {
        // SessionStarted / SessionEnded have no message_index; they must not reset the counter.
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);

        await client.AppendToStreamAsync(
            streamName,
            StreamState.NoStream,
            [
                EventSerializer.Serialize(new SessionStarted { AgentName = "a", Model = "m", Timestamp = IndexPts }),
                EventSerializer.Serialize(UserMsg("hi", "m-1", 5)),
                EventSerializer.Serialize(new SessionEnded { Reason = "done", Timestamp = IndexPts }),
            ]
        );

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(client, sessionId);

        await Assert.That(next).IsEqualTo(6);
    }

    [Test]
    public async Task ApprovalRequestRoundTripsThroughKurrentDB() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);
        var             now        = DateTimeOffset.UtcNow;

        var fc = new FunctionCallContent(
            "call-1",
            "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" }
        );
        var approval = new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc);

        var carrier = new ChatMessage(
            ChatRole.Assistant,
            [
                new TextContent("Drafting…"),
                approval,
            ]
        ) { MessageId = "asst-msg-1" };

        var events = ChatMessageConverter.ToEvents(carrier, messageIndex: 0, timestamp: now)
            .Select(e => EventSerializer.Serialize(e))
            .ToArray();
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, events);

        // Read back via the converter path the provider uses internally — same grouping, same merge.
        var resolved = new List<object>();

        await foreach (var re in client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start)) {
            if (EventSerializer.Deserialize(re) is { } e) resolved.Add(e);
        }

        var issued  = resolved.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);
        var rebuilt = ChatMessageConverter.MergeIntoChatMessage(resolved, issued);

        await Assert.That(rebuilt).IsNotNull();
        await Assert.That(rebuilt!.Role).IsEqualTo(ChatRole.Assistant);
        await Assert.That(rebuilt.MessageId).IsEqualTo("asst-msg-1");
        await Assert.That(rebuilt.Contents.OfType<TextContent>().Single().Text).IsEqualTo("Drafting…");

        await Assert.That(
                ((FunctionCallContent)rebuilt.Contents.OfType<ToolApprovalRequestContent>()
                    .Single()
                    .ToolCall).Name
            )
            .IsEqualTo("send_email");
    }

    [Test]
    public async Task ReadNextMessageIndex_AfterApprovalOnlyTurns_ContinuesCorrectly() {
        using var client     = db.CreateClient();
        var       sessionId  = Guid.NewGuid().ToString("N");
        var       streamName = StreamNames.AgentSession(sessionId);
        var       now        = DateTimeOffset.UtcNow;

        var fc = new FunctionCallContent(
            "call-1",
            "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" }
        );

        // Index 0: regular user text.
        var turn0 = new ChatMessage(ChatRole.User, "Send the email.") { MessageId = "user-0" };

        // Index 1: assistant approval-only (no text).
        var turn1 = new ChatMessage(
            ChatRole.Assistant,
            [
                new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc),
            ]
        ) { MessageId = "asst-1" };

        // Index 2: user approval-only response.
        var turn2 = new ChatMessage(
            ChatRole.User,
            [
                new ToolApprovalResponseContent(requestId: "call-1", approved: true, toolCall: fc),
            ]
        ) { MessageId = "user-2" };

        var events = ChatMessageConverter.ToEvents(turn0, messageIndex: 0, timestamp: now)
            .Concat(ChatMessageConverter.ToEvents(turn1, messageIndex: 1, timestamp: now))
            .Concat(ChatMessageConverter.ToEvents(turn2, messageIndex: 2, timestamp: now))
            .Select(e => EventSerializer.Serialize(e))
            .ToArray();
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, events);

        var nextIndex = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(client, sessionId);

        // Approval-only turns at index 1 and 2 must still bump the counter via marker events.
        await Assert.That(nextIndex).IsEqualTo(3);
    }

    [Test]
    public async Task ApprovalResponseRoundTripsThroughKurrentDB() {
        await using var client     = db.CreateClient();
        var             sessionId  = Guid.NewGuid().ToString("N");
        var             streamName = StreamNames.AgentSession(sessionId);
        var             now        = DateTimeOffset.UtcNow;

        var fc = new FunctionCallContent(
            "call-1",
            "send_email",
            new Dictionary<string, object?> { ["to"] = "alice" }
        );

        var assistant = new ChatMessage(
            ChatRole.Assistant,
            [
                new ToolApprovalRequestContent(requestId: "call-1", toolCall: fc),
            ]
        ) { MessageId = "asst-msg-1" };

        var userMsg = new ChatMessage(
            ChatRole.User,
            [
                new ToolApprovalResponseContent(requestId: "call-1", approved: true, toolCall: fc) { Reason = "Looks good." },
            ]
        ) { MessageId = "user-msg-2" };

        var events = ChatMessageConverter.ToEvents(assistant, messageIndex: 0, timestamp: now)
            .Concat(ChatMessageConverter.ToEvents(userMsg, messageIndex: 1, timestamp: now))
            .Select(e => EventSerializer.Serialize(e))
            .ToArray();
        await client.AppendToStreamAsync(streamName, StreamState.NoStream, events);

        // Read back via the same converter path the provider uses internally.
        var resolved = new List<object>();

        await foreach (var re in client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start)) {
            if (EventSerializer.Deserialize(re) is { } e) resolved.Add(e);
        }

        // Group by message_id and merge per-group, mirroring ProvideChatHistoryAsync.
        var groups        = new List<List<object>>();
        var byMessageId   = new Dictionary<string, int>();
        var issuedByReqId = resolved.OfType<InterruptIssued>().ToDictionary(e => e.RequestId);

        foreach (var ev in resolved) {
            string? key = ev switch {
                UserMessageReceived x         => x.HasMessageId ? x.MessageId : null,
                AssistantTextGenerated x      => x.HasMessageId ? x.MessageId : null,
                AssistantToolCallsGenerated x => x.HasMessageId ? x.MessageId : null,
                AssistantThinkingGenerated x  => x.HasMessageId ? x.MessageId : null,
                ToolResultReceived x          => x.HasMessageId ? x.MessageId : null,
                InterruptIssued x             => x.HasMessageId ? x.MessageId : null,
                InterruptResolved x           => x.HasMessageId ? x.MessageId : null,
                _                             => null,
            };

            if (key is { } k && byMessageId.TryGetValue(k, out var gi)) groups[gi].Add(ev);
            else {
                groups.Add([ev]);
                if (key is not null) byMessageId[key] = groups.Count - 1;
            }
        }

        var rebuiltMessages = groups
            .Select(g => ChatMessageConverter.MergeIntoChatMessage(g, issuedByReqId))
            .Where(m => m is not null)
            .ToList();

        await Assert.That(rebuiltMessages.Count).IsEqualTo(2);

        var rebuiltUser = rebuiltMessages.Last()!;
        await Assert.That(rebuiltUser.Role).IsEqualTo(ChatRole.User);
        await Assert.That(rebuiltUser.MessageId).IsEqualTo("user-msg-2");

        var resp = rebuiltUser.Contents.OfType<ToolApprovalResponseContent>().Single();
        await Assert.That(resp.Approved).IsTrue();
        await Assert.That(resp.Reason).IsEqualTo("Looks good.");
        var rebuiltFc = (FunctionCallContent)resp.ToolCall;
        await Assert.That(rebuiltFc.CallId).IsEqualTo("call-1");
        await Assert.That(rebuiltFc.Name).IsEqualTo("send_email");
        await Assert.That(rebuiltFc.Arguments!["to"]?.ToString()).IsEqualTo("alice");
    }
}
