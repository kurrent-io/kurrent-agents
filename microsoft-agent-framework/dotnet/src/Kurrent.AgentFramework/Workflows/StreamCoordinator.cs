using System.Text.Json;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Workflows;

/// <summary>
/// Coordinates multiple agent instances via KurrentDB streams.
/// Agents publish work items to a shared stream and consume them
/// via persistent subscriptions (competing consumers pattern).
/// Enables load-balanced, cross-process agent coordination.
/// </summary>
public sealed class StreamCoordinator(KurrentDBClient client) {
    static readonly JsonSerializerOptions JsonOptions = new() {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// Publish a work item to a coordination stream.
    /// Any agent subscribed to this stream can pick it up.
    /// </summary>
    public async Task PublishAsync<T>(string stream, string eventType, T payload, CancellationToken ct = default) {
        var data = JsonSerializer.SerializeToUtf8Bytes(payload, JsonOptions);

        await client.AppendToStreamAsync(
                stream,
                StreamState.Any,
                [new(Uuid.NewUuid(), eventType, data)],
                cancellationToken: ct
            )
            .ConfigureAwait(false);
    }

    /// <summary>
    /// Subscribe to a coordination stream via catch-up subscription.
    /// Each event is delivered to the handler in order.
    /// </summary>
    public async Task SubscribeAsync<T>(
            string                                   stream,
            Func<T, string, CancellationToken, Task> handler,
            CancellationToken                        ct = default
        ) where T : class {
        var subscription = client.SubscribeToStream(stream, FromStream.Start, cancellationToken: ct);

        await using var _ = ((IAsyncDisposable)subscription).ConfigureAwait(false);

        await foreach (var message in subscription.Messages.WithCancellation(ct).ConfigureAwait(false)) {
            if (message is not StreamMessage.Event(var resolvedEvent)) continue;

            var payload = JsonSerializer.Deserialize<T>(resolvedEvent.Event.Data.Span, JsonOptions);

            if (payload is null) continue;

            await handler(payload, resolvedEvent.Event.EventType, ct).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// Publish a result back to a result stream, correlated with the original work item.
    /// </summary>
    public Task PublishResultAsync<T>(
            string            stream,
            string            correlationId,
            T                 result,
            CancellationToken ct = default
        ) {
        var metadata = new Dictionary<string, object?> { ["$correlationId"] = correlationId, };

        var data          = JsonSerializer.SerializeToUtf8Bytes(result, JsonOptions);
        var metadataBytes = JsonSerializer.SerializeToUtf8Bytes(metadata, JsonOptions);

        return client.AppendToStreamAsync(
            stream,
            StreamState.Any,
            [new(Uuid.NewUuid(), "AgentResult", data, metadataBytes)],
            cancellationToken: ct
        );
    }
}
