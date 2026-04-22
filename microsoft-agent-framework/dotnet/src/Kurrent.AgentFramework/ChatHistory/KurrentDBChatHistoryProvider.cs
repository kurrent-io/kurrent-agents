using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Capture;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.ChatHistory;

/// <summary>
/// Persists chat history as rich typed events in KurrentDB.
/// Each message is decomposed into domain events (UserMessageReceived, AssistantTextGenerated,
/// AssistantToolCallsGenerated, ToolResultReceived) on write, and reconstructed on read.
/// Token usage is attached as event metadata on assistant messages when a UsageCapture is provided.
/// Emits SessionStarted on first write and SessionEnded via EndSessionAsync.
/// </summary>
public sealed class KurrentDBChatHistoryProvider(
        KurrentDBClient client,
        string          sessionId,
        UsageCapture?   usageCapture = null,
        string?         agentName    = null,
        string?         modelName    = null,
        string?         appName      = null,
        string?         userId       = null,
        string?         tenantId     = null
    ) : ChatHistoryProvider {

    readonly ProviderSessionState<SessionState> _sessionState = new(
        stateInitializer: _ => new() { SessionId = sessionId },
        stateKey: nameof(KurrentDBChatHistoryProvider)
    );

    bool _sessionStarted;

    /// <summary>
    /// Load conversation history from the KurrentDB stream.
    /// </summary>
    protected override async ValueTask<IEnumerable<ChatMessage>> ProvideChatHistoryAsync(
            InvokingContext   context,
            CancellationToken cancellationToken = default
        ) {
        var state      = _sessionState.GetOrInitializeState(context.Session);
        var streamName = StreamNames.AgentSession(state.SessionId);

        var messages = new List<ChatMessage>();
        var maxIndex = -1;

        try {
            var result = client.ReadStreamAsync(
                Direction.Forwards,
                streamName,
                StreamPosition.Start,
                cancellationToken: cancellationToken
            );

            await foreach (var resolvedEvent in result.ConfigureAwait(false)) {
                _sessionStarted = true; // stream exists, session was already started

                var domainEvent = EventSerializer.Deserialize(resolvedEvent);

                if (domainEvent is null) continue;

                var idx = GetMessageIndex(domainEvent);
                if (idx > maxIndex) maxIndex = idx;

                var chatMessage = ChatMessageConverter.ToChatMessage(domainEvent);

                if (chatMessage is not null) {
                    messages.Add(chatMessage);
                }
            }
        } catch (StreamNotFoundException) {
            // First interaction — no history yet
        }

        // Seed monotonic message_index for the next Store call (continues across
        // turns, rehydrates on new provider instances over existing streams).
        state.NextMessageIndex = maxIndex + 1;
        _sessionState.SaveState(context.Session, state);

        return messages;
    }

    /// <summary>
    /// Persist new messages as typed events to KurrentDB.
    /// Emits SessionStarted as the first event in a new stream.
    /// </summary>
    protected override async ValueTask StoreChatHistoryAsync(InvokedContext context, CancellationToken cancellationToken = default) {
        var state      = _sessionState.GetOrInitializeState(context.Session);
        var streamName = StreamNames.AgentSession(state.SessionId);

        var now          = DateTimeOffset.UtcNow;
        var events       = new List<EventData>();
        var messageIndex = state.NextMessageIndex;

        // Emit SessionStarted as the first event in a new stream
        if (!_sessionStarted) {
            events.Add(EventSerializer.Serialize(new SessionStarted(
                AppName:           appName,
                AgentName:         agentName ?? context.Agent.Name,
                Model:             modelName,
                TenantId:          tenantId,
                UserId:            userId,
                AgentConfig:       null,
                PreviousSessionId: null,
                Timestamp:         now
            )));
            _sessionStarted = true;
        }

        // Decompose request messages into typed events
        foreach (var message in context.RequestMessages) {
            var metadata = ToMetadata(message.AdditionalProperties);
            events.AddRange(ChatMessageConverter.ToEvents(message, messageIndex, now).Select(e => EventSerializer.Serialize(e, metadata: metadata)));
            messageIndex++;
        }

        // Decompose response messages into typed events, with usage on assistant messages
        if (context.ResponseMessages is not null) {
            foreach (var message in context.ResponseMessages) {
                var metadata = ToMetadata(message.AdditionalProperties);

                if (message.MessageId is not null
                 && usageCapture is not null
                 && usageCapture.TryGet(message.MessageId, out var usage)
                 && usage is not null) {
                    metadata = MergeUsageIntoMetadata(metadata, usage);
                }

                events.AddRange(ChatMessageConverter.ToEvents(message, messageIndex, now).Select(e => EventSerializer.Serialize(e, metadata: metadata)));
                messageIndex++;
            }

            usageCapture?.Clear();
        }

        if (events.Count > 0) {
            await client.AppendToStreamAsync(streamName, StreamState.Any, events, cancellationToken: cancellationToken).ConfigureAwait(false);
        }

        state.NextMessageIndex = messageIndex;
        _sessionState.SaveState(context.Session, state);
    }

    /// <summary>
    /// Write a SessionEnded event to close the session stream.
    /// </summary>
    public async Task EndSessionAsync(string? reason = null, CancellationToken cancellationToken = default) {
        var streamName = StreamNames.AgentSession(sessionId);

        await client.AppendToStreamAsync(
            streamName,
            StreamState.Any,
            [EventSerializer.Serialize(new SessionEnded(Reason: reason, Timestamp: DateTimeOffset.UtcNow))],
            cancellationToken: cancellationToken
        ).ConfigureAwait(false);
    }

    /// <summary>
    /// Compute the next <c>message_index</c> for an existing session by scanning the
    /// stream for the highest index stamped on any chat event. Returns 0 when the
    /// stream does not exist or contains no chat events yet.
    /// </summary>
    /// <remarks>
    /// This is the same logic <see cref="ProvideChatHistoryAsync"/> uses internally;
    /// exposed as a public helper for callers that want to seed a new provider over
    /// an existing session stream without going through a full MAF invocation, and
    /// for test coverage of the index-continuation rule.
    /// </remarks>
    public static async Task<int> ReadNextMessageIndexAsync(
            KurrentDBClient   client,
            string            sessionId,
            CancellationToken cancellationToken = default
        ) {
        var streamName = StreamNames.AgentSession(sessionId);
        var maxIndex   = -1;

        try {
            var events = client.ReadStreamAsync(
                Direction.Forwards,
                streamName,
                StreamPosition.Start,
                cancellationToken: cancellationToken
            );

            await foreach (var resolved in events.ConfigureAwait(false)) {
                var domainEvent = EventSerializer.Deserialize(resolved);

                if (domainEvent is null) continue;

                var idx = GetMessageIndex(domainEvent);
                if (idx > maxIndex) maxIndex = idx;
            }
        } catch (StreamNotFoundException) {
            // No stream yet — fall through to zero.
        }

        return maxIndex + 1;
    }

    static int GetMessageIndex(object domainEvent) => domainEvent switch {
        UserMessageReceived x         => x.MessageIndex,
        AssistantTextGenerated x      => x.MessageIndex,
        AssistantToolCallsGenerated x => x.MessageIndex,
        AssistantThinkingGenerated x  => x.MessageIndex,
        ToolResultReceived x          => x.MessageIndex,
        _                             => -1,
    };

    static Dictionary<string, object?>? ToMetadata(AdditionalPropertiesDictionary? props) =>
        props is { Count: > 0 } ? props.ToDictionary(kv => kv.Key, kv => kv.Value) : null;

    static Dictionary<string, object?> MergeUsageIntoMetadata(IDictionary<string, object?>? metadata, UsageDetails usage) {
        var result = metadata is not null
            ? new Dictionary<string, object?>(metadata)
            : new Dictionary<string, object?>();

        result["$usage"] = new Dictionary<string, object?> {
            ["input_tokens"]        = usage.InputTokenCount,
            ["output_tokens"]       = usage.OutputTokenCount,
            ["total_tokens"]        = usage.TotalTokenCount,
            ["cached_input_tokens"] = usage.CachedInputTokenCount,
            ["reasoning_tokens"]    = usage.ReasoningTokenCount,
            ["additional_counts"]   = usage.AdditionalCounts,
        };

        return result;
    }

    sealed class SessionState {
        public string SessionId         { get; set; } = "";
        public int    NextMessageIndex  { get; set; }
    }
}
