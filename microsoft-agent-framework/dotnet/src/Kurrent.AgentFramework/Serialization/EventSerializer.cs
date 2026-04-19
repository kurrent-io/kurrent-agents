using System.Text.Json;
using System.Text.Json.Serialization;
using KurrentDB.Client;

namespace Kurrent.AgentFramework.Serialization;

/// <summary>
/// Serializes/deserializes domain events to/from KurrentDB EventData.
/// </summary>
public static class EventSerializer {
    static readonly JsonSerializerOptions JsonOptions = new() {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// Serialize a domain event to KurrentDB EventData.
    /// Optional metadata dictionary is stored in the event metadata slot.
    /// </summary>
    public static EventData Serialize(
            object                          @event,
            Uuid?                           eventId  = null,
            IDictionary<string, object?>?   metadata = null
        ) {
        var typeName = EventTypeMap.GetEventTypeName(@event.GetType());
        var data     = JsonSerializer.SerializeToUtf8Bytes(@event, @event.GetType(), JsonOptions);

        var metadataBytes = metadata is { Count: > 0 }
            ? JsonSerializer.SerializeToUtf8Bytes(metadata, JsonOptions)
            : null;

        return new(eventId ?? Uuid.NewUuid(), typeName, data, metadataBytes);
    }

    /// <summary>
    /// Deserialize a KurrentDB resolved event back to a domain event.
    /// Returns null if the event type is unknown.
    /// </summary>
    public static object? Deserialize(ResolvedEvent resolvedEvent) {
        var clrType = EventTypeMap.GetClrType(resolvedEvent.Event.EventType);

        return clrType is null ? null : JsonSerializer.Deserialize(resolvedEvent.Event.Data.Span, clrType, JsonOptions);
    }
}
