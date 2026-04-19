using Kurrent.AgentFramework.ChatHistory;
using Kurrent.AgentFramework.Events;
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
            .ReadStreamAsync(Direction.Forwards, StreamName.ForSession(sessionId), StreamPosition.Start)
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
            .ReadStreamAsync(Direction.Forwards, StreamName.ForSession(sessionId), StreamPosition.Start)
            .SingleAsync();

        var ended = EventSerializer.Deserialize(read) as SessionEnded;
        await Assert.That(ended).IsNotNull();
        await Assert.That(ended!.Reason).IsNull();
    }
}
