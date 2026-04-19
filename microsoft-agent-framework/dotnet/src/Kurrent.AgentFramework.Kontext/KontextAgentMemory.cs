using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Kurrent.AgentFramework.Memory;
using Kurrent.Kontext;

namespace Kurrent.AgentFramework.Kontext;

/// <summary>
/// <see cref="IAgentMemory"/> backed by Kurrent.Kontext hybrid search (BM25 + vector).
/// Facts are written as events to Kontext's memory stream and recalled using semantic + keyword search.
/// </summary>
public sealed class KontextAgentMemory(
        IKontextService          kontext,
        IKontextClient           client,
        KontextAgentMemoryConfig memoryConfig
    ) : IAgentMemory {
    public async IAsyncEnumerable<string> RecallAsync(
            string                                     query,
            [EnumeratorCancellation] CancellationToken ct = default
        ) {
        var hits = await kontext.RecallAsync(query, question: query).ConfigureAwait(false);

        foreach (var hit in hits) {
            ct.ThrowIfCancellationRequested();

            var stream = hit.GetValueOrDefault("stream")?.ToString();

            if (stream is null) continue;
            if (!hit.TryGetValue("eventNumber", out var en) || !TryToInt64(en, out var eventNumber)) continue;

            await foreach (var evt in client.ReadAsync(stream, eventNumber, eventNumber).WithCancellation(ct).ConfigureAwait(false)) {
                if (evt.Data?.ValueKind != JsonValueKind.Object) continue;
                if (!evt.Data.Value.TryGetProperty("fact", out var factProp)) continue;

                var fact = factProp.GetString();

                if (!string.IsNullOrWhiteSpace(fact))
                    yield return fact;
            }
        }
    }

    public Task RetainAsync(string fact, CancellationToken ct = default) {
        if (string.IsNullOrWhiteSpace(fact)) return Task.CompletedTask;

        ct.ThrowIfCancellationRequested();

        var data = JsonSerializer.SerializeToUtf8Bytes(new {
            fact,
            retainedAt = DateTime.UtcNow.ToString("O"),
        });

        // IKontextClient.WriteAsync does not accept a CancellationToken; the check above
        // aborts the call if cancellation is requested before the write is issued.
        return client.WriteAsync(memoryConfig.Stream, memoryConfig.EventType, data);
    }

    /// <summary>
    /// Tolerantly parses an untyped recall-hit value as <see cref="long"/>.
    /// Hit values can arrive as numeric primitives, <see cref="JsonElement"/>, or <see cref="string"/>
    /// depending on the Kontext transport; anything unrecognised is treated as "skip this hit".
    /// </summary>
    static bool TryToInt64(object? value, out long result) {
        switch (value) {
            case null:
                result = 0;
                return false;
            case long l:
                result = l;
                return true;
            case int i:
                result = i;
                return true;
            case short s:
                result = s;
                return true;
            case byte b:
                result = b;
                return true;
            case uint ui:
                result = ui;
                return true;
            case ushort us:
                result = us;
                return true;
            case JsonElement je when je.ValueKind == JsonValueKind.Number:
                return je.TryGetInt64(out result);
            case JsonElement je when je.ValueKind == JsonValueKind.String:
                return long.TryParse(je.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out result);
            case string str:
                return long.TryParse(str, NumberStyles.Integer, CultureInfo.InvariantCulture, out result);
            default:
                result = 0;
                return false;
        }
    }
}
