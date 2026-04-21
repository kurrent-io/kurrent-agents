using System.Runtime.CompilerServices;
using Kurrent.AgentFramework.Events;
using Kurrent.AgentFramework.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Memory;

/// <summary>
/// Simple KurrentDB-backed agent memory: facts are appended as events to a single
/// stream, and recall reads them back. No indexing, no embeddings.
/// Adequate for small fact sets (dozens to low hundreds). For larger or
/// semantically-indexed memory, use <c>Kurrent.AgentFramework.Kontext</c>.
/// <para>
/// <b>Scope:</b> facts are stored in a single shared stream (default: <c>"AgentMemory"</c>).
/// This means memory is <b>global across all sessions/tenants</b> using the same process.
/// Multi-tenant deployments should either (a) register one memory instance per tenant with a
/// per-tenant <paramref name="streamName"/>, or (b) provide a custom <see cref="IAgentMemory"/>
/// implementation that scopes recall/retain by tenant or user.
/// </para>
/// </summary>
public sealed class KurrentDBAgentMemory(KurrentDBClient client, string streamName = "AgentMemory") : IAgentMemory {
    /// <summary>
    /// Returns every retained fact, newest first. The <paramref name="query"/> is ignored —
    /// this implementation does not filter. Consumers (LLMs) do the matching themselves.
    /// </summary>
    public async IAsyncEnumerable<string> RecallAsync(string query, [EnumeratorCancellation] CancellationToken ct = default) {
        // StreamNotFoundException is thrown lazily during enumeration (not at call site),
        // so the MoveNextAsync loop wraps both construction and iteration in try/catch.
        var events = client.ReadStreamAsync(Direction.Backwards, streamName, StreamPosition.End, cancellationToken: ct);

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

        var eventData = EventSerializer.Serialize(new FactRetained(fact, DateTimeOffset.UtcNow));

        await client.AppendToStreamAsync(
            streamName,
            StreamState.Any,
            [eventData],
            cancellationToken: ct
        ).ConfigureAwait(false);
    }
}
