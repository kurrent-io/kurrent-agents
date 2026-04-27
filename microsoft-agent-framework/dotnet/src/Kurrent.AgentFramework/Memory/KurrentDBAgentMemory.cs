using System.Runtime.CompilerServices;
using Google.Protobuf.WellKnownTypes;
using Kurrent.Agent.Schema;
using Kurrent.Agent.Schema.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Memory;

/// <summary>
/// Simple KurrentDB-backed agent memory: facts are appended as events to a
/// per-app, per-user stream, and recall reads them back. No indexing, no
/// embeddings. Adequate for small fact sets (dozens to low hundreds). For
/// larger or semantically-indexed memory, provide a custom
/// <see cref="IAgentMemory"/> implementation.
/// <para>
/// <b>Scope.</b> By default, facts land in <c>AgentMemory-{appName}-{userId}</c>
/// (canonical per <c>SCHEMA_v2.md §2.1</c>), matching the convention used by
/// every other integration in this monorepo. Pass <paramref name="streamName"/>
/// to override — e.g. a deliberately shared cross-tenant stream or a custom
/// scope — in which case <paramref name="appName"/> and <paramref name="userId"/>
/// are not needed.
/// </para>
/// </summary>
public sealed class KurrentDBAgentMemory : IAgentMemory {
    readonly KurrentDBClient _client;
    readonly string          _streamName;

    public KurrentDBAgentMemory(
            KurrentDBClient client,
            string?         appName    = null,
            string?         userId     = null,
            string?         streamName = null
        ) {
        _client = client;

        if (streamName is not null) {
            // Reject blank overrides at the boundary — an empty stream name would
            // otherwise surface as an opaque KurrentDB error on the first append.
            if (string.IsNullOrWhiteSpace(streamName))
                throw new ArgumentException("streamName must be non-empty when provided.", nameof(streamName));
            _streamName = streamName;
            return;
        }

        // Canonical path: empty identifiers would silently collapse per-tenant
        // scope into a shared stream (e.g. "AgentMemory--"), so reject them.
        if (string.IsNullOrWhiteSpace(appName))
            throw new ArgumentException("appName must be non-empty (or pass streamName).", nameof(appName));
        if (string.IsNullOrWhiteSpace(userId))
            throw new ArgumentException("userId must be non-empty (or pass streamName).", nameof(userId));

        _streamName = StreamNames.AgentMemory(appName, userId);
    }

    /// <summary>
    /// Returns every retained fact, newest first. The <paramref name="query"/> is ignored —
    /// this implementation does not filter. Consumers (LLMs) do the matching themselves.
    /// </summary>
    public async IAsyncEnumerable<string> RecallAsync(string query, [EnumeratorCancellation] CancellationToken ct = default) {
        // StreamNotFoundException is thrown lazily during enumeration (not at call site),
        // so the MoveNextAsync loop wraps both construction and iteration in try/catch.
        var events = _client.ReadStreamAsync(Direction.Backwards, _streamName, StreamPosition.End, cancellationToken: ct);

        await using var enumerator = events.ConfigureAwait(false).GetAsyncEnumerator();

        while (true) {
            ResolvedEvent resolved;

            try {
                if (!await enumerator.MoveNextAsync()) yield break;
                resolved = enumerator.Current;
            } catch (StreamNotFoundException) {
                yield break;
            }

            if (EventSerializer.Deserialize(resolved) is FactRetained { Fact: var fact } && !string.IsNullOrWhiteSpace(fact))
                yield return fact;
        }
    }

    public async Task RetainAsync(string fact, CancellationToken ct = default) {
        if (string.IsNullOrWhiteSpace(fact)) return;

        var eventData = EventSerializer.Serialize(new FactRetained {
            Fact       = fact,
            RetainedAt = Timestamp.FromDateTimeOffset(DateTimeOffset.UtcNow),
        });

        await _client.AppendToStreamAsync(
            _streamName,
            StreamState.Any,
            [eventData],
            cancellationToken: ct
        ).ConfigureAwait(false);
    }
}
