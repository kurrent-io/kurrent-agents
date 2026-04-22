using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.ChatHistory;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

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
        await Assert.That(ended!.Reason).IsNull();
    }

    // --- message_index continuation (qodo review, DEV-1548) ---

    static readonly DateTimeOffset IndexTs = new(2026, 4, 17, 12, 0, 0, TimeSpan.Zero);

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
            EventSerializer.Serialize(new UserMessageReceived("q", "m-1", "user", IndexTs, 0, IndexTs)),
            EventSerializer.Serialize(new AssistantTextGenerated("a", "m-2", "agent", IndexTs, 1, IndexTs)),
            EventSerializer.Serialize(new UserMessageReceived("q2", "m-3", "user", IndexTs, 2, IndexTs)),
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
            EventSerializer.Serialize(new SessionStarted(
                AppName:           null,
                AgentName:         "a",
                Model:             "m",
                TenantId:          null,
                UserId:            null,
                AgentConfig:       null,
                PreviousSessionId: null,
                Timestamp:         IndexTs)),
            EventSerializer.Serialize(new UserMessageReceived("hi", "m-1", "user", IndexTs, 5, IndexTs)),
            EventSerializer.Serialize(new SessionEnded("done", IndexTs)),
        ]);

        var next = await KurrentDBChatHistoryProvider.ReadNextMessageIndexAsync(client, sessionId);

        await Assert.That(next).IsEqualTo(6);
    }
}
