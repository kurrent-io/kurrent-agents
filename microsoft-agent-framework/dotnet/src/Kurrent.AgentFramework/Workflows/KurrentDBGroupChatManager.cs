using System.Text.Json;
using KurrentDB.Client;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;

namespace Kurrent.AgentFramework.Workflows;

/// <summary>
/// Event emitted when an agent takes a turn in a group chat.
/// </summary>
public sealed record AgentTurnTaken(
        string         AgentId,
        string?        AgentName,
        string?        Content,
        int            IterationNumber,
        DateTimeOffset Timestamp
    );

/// <summary>
/// Event emitted when the group chat terminates.
/// </summary>
public sealed record GroupChatCompleted(
        string?        Reason,
        int            TotalIterations,
        DateTimeOffset Timestamp
    );

/// <summary>
/// A GroupChatManager that persists every agent turn as an event in a KurrentDB stream.
/// Enables durable group chat: agents can rejoin after restarts, and the full
/// multi-agent conversation is an auditable, replayable event stream.
/// </summary>
public class KurrentDBGroupChatManager : GroupChatManager {
    readonly KurrentDBClient                                                        _client;
    readonly string                                                                 _streamName;
    readonly IReadOnlyList<AIAgent>                                                 _agents;
    readonly Func<IReadOnlyList<ChatMessage>, IReadOnlyList<AIAgent>, int, AIAgent> _selectNext;
    readonly Func<IReadOnlyList<ChatMessage>, int, bool>?                           _shouldTerminate;

    static readonly JsonSerializerOptions JsonOptions = new() {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// Create a KurrentDB-backed group chat manager.
    /// </summary>
    /// <param name="client">KurrentDB client.</param>
    /// <param name="chatId">Unique identifier for this group chat — used as the stream name suffix.</param>
    /// <param name="agents">The participating agents.</param>
    /// <param name="selectNext">Strategy for selecting the next agent. Receives history, agents, and iteration count.</param>
    /// <param name="shouldTerminate">Optional termination strategy. Receives history and iteration count.</param>
    /// <param name="maxIterations">Maximum iterations before forced termination.</param>
    public KurrentDBGroupChatManager(
            KurrentDBClient                                                        client,
            string                                                                 chatId,
            IReadOnlyList<AIAgent>                                                 agents,
            Func<IReadOnlyList<ChatMessage>, IReadOnlyList<AIAgent>, int, AIAgent> selectNext,
            Func<IReadOnlyList<ChatMessage>, int, bool>?                           shouldTerminate = null,
            int                                                                    maxIterations   = 40
        ) {
        _client               = client;
        _streamName           = $"GroupChat-{chatId}";
        _agents               = agents;
        _selectNext           = selectNext;
        _shouldTerminate      = shouldTerminate;
        MaximumIterationCount = maxIterations;
    }

    protected override async ValueTask<AIAgent> SelectNextAgentAsync(
            IReadOnlyList<ChatMessage> history,
            CancellationToken          cancellationToken = default
        ) {
        var selected = _selectNext(history, _agents, IterationCount);

        // Record the turn
        var lastMessage = history.Count > 0 ? history[^1] : null;

        var evt = new AgentTurnTaken(
            AgentId: selected.Id ?? selected.Name ?? "unknown",
            AgentName: selected.Name,
            Content: lastMessage?.Text,
            IterationNumber: IterationCount,
            Timestamp: DateTimeOffset.UtcNow
        );

        var data = JsonSerializer.SerializeToUtf8Bytes(evt, JsonOptions);

        await _client.AppendToStreamAsync(
                _streamName,
                StreamState.Any,
                [new(Uuid.NewUuid(), "AgentTurnTaken", data)],
                cancellationToken: cancellationToken
            )
            .ConfigureAwait(false);

        return selected;
    }

    protected override async ValueTask<bool> ShouldTerminateAsync(
            IReadOnlyList<ChatMessage> history,
            CancellationToken          cancellationToken = default
        ) {
        var terminate = _shouldTerminate?.Invoke(history, IterationCount)
         ?? await base.ShouldTerminateAsync(history, cancellationToken).ConfigureAwait(false);

        if (terminate) {
            var evt = new GroupChatCompleted(
                Reason: "terminated",
                TotalIterations: IterationCount,
                Timestamp: DateTimeOffset.UtcNow
            );

            var data = JsonSerializer.SerializeToUtf8Bytes(evt, JsonOptions);

            await _client.AppendToStreamAsync(
                    _streamName,
                    StreamState.Any,
                    [new(Uuid.NewUuid(), "GroupChatCompleted", data)],
                    cancellationToken: cancellationToken
                )
                .ConfigureAwait(false);
        }

        return terminate;
    }

    /// <summary>
    /// Read the full group chat history from KurrentDB.
    /// Useful for auditing or replaying the multi-agent conversation.
    /// </summary>
    public async Task<IReadOnlyList<AgentTurnTaken>> ReadHistoryAsync(CancellationToken ct = default) {
        var turns = new List<AgentTurnTaken>();

        try {
            var events = _client.ReadStreamAsync(Direction.Forwards, _streamName, StreamPosition.Start, cancellationToken: ct);

            await foreach (var resolvedEvent in events.ConfigureAwait(false)) {
                if (resolvedEvent.Event.EventType != "AgentTurnTaken") continue;

                var turn = JsonSerializer.Deserialize<AgentTurnTaken>(resolvedEvent.Event.Data.Span, JsonOptions);
                if (turn is not null) turns.Add(turn);
            }
        } catch (StreamNotFoundException) { }

        return turns;
    }
}
