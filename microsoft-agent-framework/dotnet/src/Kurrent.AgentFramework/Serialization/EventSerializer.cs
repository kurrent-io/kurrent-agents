using System.Collections.Concurrent;
using System.Reflection;
using System.Text;
using System.Text.Json;
using Google.Protobuf;
using Kurrent.Agent.Schema;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Serialization;

/// <summary>
/// Thin adapter that packages canonical <see cref="Kurrent.Agent.Schema.Events"/> messages
/// into <see cref="EventData"/> using the shared <see cref="SchemaJsonOptions"/> wire format
/// and <see cref="EventTypeMap"/> naming. Stamps <c>$schema_version</c> on metadata per
/// SCHEMA_v2 §9. Schema 0.2.0 swaps hand-written records for protobuf-generated messages,
/// so the codec goes through <c>SchemaJsonOptions.ToJson</c> / <c>FromJson&lt;T&gt;</c>
/// (the sanctioned JSON entry point) rather than <see cref="JsonSerializer"/>.
/// </summary>
public static class EventSerializer {
    const string SchemaVersionMetaKey = "$schema_version";

    static readonly IReadOnlyDictionary<Type, string> NamesByType =
        EventTypeMap.All.ToDictionary(p => p.Value, p => p.Key);

    static readonly MethodInfo FromJsonGeneric =
        typeof(SchemaJsonOptions).GetMethod(nameof(SchemaJsonOptions.FromJson))!;

    static readonly ConcurrentDictionary<Type, MethodInfo> FromJsonCache = new();

    /// <summary>
    /// Serialize a canonical event to <see cref="EventData"/>. Caller-supplied metadata
    /// is preserved; <c>$schema_version</c> is always stamped last and wins over any
    /// caller-supplied value to keep the wire version authoritative per SCHEMA_v2 §9.
    /// </summary>
    public static EventData Serialize(
            object                        @event,
            Uuid?                         eventId  = null,
            IDictionary<string, object?>? metadata = null
        ) {
        if (@event is not IMessage message)
            throw new ArgumentException(
                $"Event must be a protobuf IMessage; got {@event.GetType().FullName}.",
                nameof(@event));

        if (!NamesByType.TryGetValue(@event.GetType(), out var typeName))
            throw new ArgumentException(
                $"Unknown canonical event type: {@event.GetType().FullName}.",
                nameof(@event));

        var data = Encoding.UTF8.GetBytes(SchemaJsonOptions.ToJson(message));

        var effective = metadata is not null
            ? new Dictionary<string, object?>(metadata)
            : new Dictionary<string, object?>();

        // Stamp last so the writer's schema version always wins — callers cannot
        // forge a different version by supplying it in metadata.
        effective[SchemaVersionMetaKey] = SchemaVersion.Current;

        var metadataBytes = JsonSerializer.SerializeToUtf8Bytes(effective);

        return new(eventId ?? Uuid.NewUuid(), typeName, data, metadataBytes);
    }

    /// <summary>
    /// Deserialize a canonical event from a <see cref="ResolvedEvent"/>. Returns
    /// <c>null</c> when the event type is not in the canonical map (framework-specific
    /// or unknown types are skipped by readers).
    /// </summary>
    public static object? Deserialize(ResolvedEvent resolvedEvent) {
        if (!EventTypeMap.All.TryGetValue(resolvedEvent.Event.EventType, out var clrType))
            return null;

        var json = Encoding.UTF8.GetString(resolvedEvent.Event.Data.Span);

        // Dispatch to the typed FromJson<T> via reflection — the sanctioned helper
        // is generic, but the event type is only known at runtime here.
        var typed = FromJsonCache.GetOrAdd(clrType, t => FromJsonGeneric.MakeGenericMethod(t));
        return typed.Invoke(null, [json]);
    }
}
