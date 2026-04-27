using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
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
        var streamName = StreamNames.AgentSession(sessionId);
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
        var ts  = Timestamp.FromDateTimeOffset(now);
        var ts2 = Timestamp.FromDateTimeOffset(now.AddMilliseconds(50));
        var ts3 = Timestamp.FromDateTimeOffset(now.AddSeconds(1));

        var started = new SessionStarted { Timestamp = ts };
        if (agentName is not null) started.AgentName = agentName;
        if (model is not null)     started.Model     = model;

        var user = new UserMessageReceived {
            MessageId    = Guid.NewGuid().ToString("N"),
            AuthorName   = "user",
            CreatedAt    = ts,
            MessageIndex = 0,
            Timestamp    = ts,
        };
        if (userText is not null) user.Content = userText;

        var asst = new AssistantTextGenerated {
            MessageId    = Guid.NewGuid().ToString("N"),
            CreatedAt    = ts,
            MessageIndex = 1,
            Timestamp    = ts2,
        };
        if (reply is not null)     asst.Content    = reply;
        if (agentName is not null) asst.AuthorName = agentName;

        var ended = new SessionEnded { Reason = "completed", Timestamp = ts3 };

        return [started, user, asst, ended];
    }
}
