using Kurrent.AgentFramework.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.IntegrationTests;

/// <summary>
/// Appends typed domain events to an <c>AgentSession-{id}</c> stream.
/// </summary>
public static class SessionWriter {
    public static async Task AppendAsync(
            KurrentDBClient       client,
            string                sessionId,
            IReadOnlyList<object> events,
            CancellationToken     ct = default
        ) {
        var streamName = StreamName.ForSession(sessionId);
        var data       = events.Select(e => EventSerializer.Serialize(e)).ToArray();
        await client.AppendToStreamAsync(streamName, StreamState.Any, data, cancellationToken: ct);
    }

    public static IReadOnlyList<object> BasicSession(
            string?         agentName = "test-agent",
            string?         model     = "claude-test",
            string?         userText  = "Hello",
            string?         reply     = "Hi there"
        ) {
        var now = DateTimeOffset.UtcNow;

        return [
            new SessionStarted(agentName, model, null, null, now),
            new UserMessageReceived(userText, Guid.NewGuid().ToString("N"), "user", now, 0, now),
            new AssistantTextGenerated(reply, Guid.NewGuid().ToString("N"), agentName, now, 1, now.AddMilliseconds(50)),
            new SessionEnded("completed", now.AddSeconds(1))
        ];
    }
}
