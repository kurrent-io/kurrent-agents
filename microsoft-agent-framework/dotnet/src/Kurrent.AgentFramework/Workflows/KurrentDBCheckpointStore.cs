using System.Text.Json;
using KurrentDB.Client;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;

namespace Kurrent.AgentFramework.Workflows;

/// <summary>
/// KurrentDB-backed checkpoint store for Agent Framework workflows.
/// Each checkpoint is stored as an event in a WorkflowCheckpoint-{sessionId} stream.
/// Supports durable, resumable, long-running workflows across process restarts.
/// </summary>
public sealed class KurrentDBCheckpointStore(KurrentDBClient client) : ICheckpointStore<JsonElement> {
    static readonly JsonSerializerOptions JsonOptions = new() {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    static string StreamFor(string sessionId) => $"WorkflowCheckpoint-{sessionId}";

    public async ValueTask<CheckpointInfo> CreateCheckpointAsync(
            string          sessionId,
            JsonElement     value,
            CheckpointInfo? parent = null
        ) {
        var checkpointId = Guid.NewGuid().ToString("N");
        var info         = new CheckpointInfo(sessionId, checkpointId);
        var streamName   = StreamFor(sessionId);

        // Store parent link + checkpoint info in metadata
        var metadata = new Dictionary<string, object?> {
            ["$checkpointId"] = checkpointId,
            ["$sessionId"]    = sessionId,
        };

        if (parent is not null) {
            metadata["$parentCheckpointId"] = parent.CheckpointId;
        }

        var metadataBytes = JsonSerializer.SerializeToUtf8Bytes(metadata, JsonOptions);
        var dataBytes     = JsonSerializer.SerializeToUtf8Bytes(value);

        await client.AppendToStreamAsync(
                streamName,
                StreamState.Any,
                [new(Uuid.NewUuid(), "WorkflowCheckpoint", dataBytes, metadataBytes)],
                cancellationToken: CancellationToken.None
            )
            .ConfigureAwait(false);

        return info;
    }

    public async ValueTask<JsonElement> RetrieveCheckpointAsync(string sessionId, CheckpointInfo key) {
        var streamName = StreamFor(sessionId);

        // Read all events and find the one matching the checkpoint ID
        var result = client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start);

        await foreach (var resolvedEvent in result.ConfigureAwait(false)) {
            if (resolvedEvent.Event.Metadata.Length == 0) continue;

            var meta = JsonSerializer.Deserialize<JsonElement>(resolvedEvent.Event.Metadata.Span);

            if (meta.TryGetProperty("$checkpointId", out var idProp)
             && idProp.GetString() == key.CheckpointId) {
                return JsonSerializer.Deserialize<JsonElement>(resolvedEvent.Event.Data.Span);
            }
        }

        throw new KeyNotFoundException(
            $"Checkpoint {key.CheckpointId} not found in stream {streamName}"
        );
    }

    public async ValueTask<IEnumerable<CheckpointInfo>> RetrieveIndexAsync(string sessionId, CheckpointInfo? withParent = null) {
        var streamName = StreamFor(sessionId);
        var results    = new List<CheckpointInfo>();

        try {
            var events = client.ReadStreamAsync(Direction.Forwards, streamName, StreamPosition.Start);

            await foreach (var resolvedEvent in events.ConfigureAwait(false)) {
                if (resolvedEvent.Event.Metadata.Length == 0) continue;

                var meta = JsonSerializer.Deserialize<JsonElement>(resolvedEvent.Event.Metadata.Span);

                if (!meta.TryGetProperty("$checkpointId", out var idProp)) continue;

                var checkpointId = idProp.GetString();

                if (checkpointId is null) continue;

                // Filter by parent if specified
                if (withParent is not null) {
                    if (!meta.TryGetProperty("$parentCheckpointId", out var parentProp)
                     || parentProp.GetString() != withParent.CheckpointId) {
                        continue;
                    }
                }

                results.Add(new CheckpointInfo(sessionId, checkpointId));
            }
        } catch (StreamNotFoundException) {
            // No checkpoints yet
        }

        return results;
    }
}
