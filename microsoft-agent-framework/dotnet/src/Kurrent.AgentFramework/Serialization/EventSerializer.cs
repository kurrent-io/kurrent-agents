using System.Text.Json;
using Kurrent.Agent.Schema;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Serialization;

/// <summary>
/// Thin adapter that packages canonical <see cref="Kurrent.Agent.Schema.Events"/> records
/// into <see cref="EventData"/> using the shared <see cref="SchemaJsonOptions.Default"/>
/// wire format and <see cref="EventTypeMap"/> naming. Stamps <c>$schema_version</c> on
/// metadata per SCHEMA_v2 §9.
/// </summary>
public static class EventSerializer {
    const string SchemaVersionMetaKey = "$schema_version";

    /// <summary>
    /// Serialize a canonical event to <see cref="EventData"/>. Any caller-supplied
    /// metadata is preserved; <c>$schema_version</c> is always added (caller value wins
    /// if explicitly provided).
    /// </summary>
    public static EventData Serialize(
            object                        @event,
            Uuid?                         eventId  = null,
            IDictionary<string, object?>? metadata = null
        ) {
        var typeName = EventTypeMap.GetName(@event.GetType());
        var data     = JsonSerializer.SerializeToUtf8Bytes(@event, @event.GetType(), SchemaJsonOptions.Default);

        var effective = new Dictionary<string, object?> { [SchemaVersionMetaKey] = SchemaVersion.Current };
        if (metadata is not null)
            foreach (var kv in metadata) effective[kv.Key] = kv.Value;

        var metadataBytes = JsonSerializer.SerializeToUtf8Bytes(effective, SchemaJsonOptions.Default);

        return new(eventId ?? Uuid.NewUuid(), typeName, data, metadataBytes);
    }

    /// <summary>
    /// Deserialize a canonical event from a <see cref="ResolvedEvent"/>. Returns
    /// <c>null</c> when the event type is not in the canonical map (framework-specific
    /// or unknown types are skipped by readers).
    /// </summary>
    public static object? Deserialize(ResolvedEvent resolvedEvent) {
        var clrType = EventTypeMap.GetType(resolvedEvent.Event.EventType);

        return clrType is null ? null : JsonSerializer.Deserialize(resolvedEvent.Event.Data.Span, clrType, SchemaJsonOptions.Default);
    }
}
