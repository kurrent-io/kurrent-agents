using System.Text.Json.Serialization;

namespace Kurrent.AgentFramework.Events;

/// <summary>
/// Emitted when a new agent session begins.
/// </summary>
public sealed record SessionStarted(
        [property: JsonPropertyName("agent_name")] string?        AgentName,
        [property: JsonPropertyName("model")]      string?        Model,
        [property: JsonPropertyName("tenant_id")]  string?        TenantId,
        [property: JsonPropertyName("user_id")]    string?        UserId,
        [property: JsonPropertyName("timestamp")]  DateTimeOffset Timestamp
    );

/// <summary>
/// Emitted when an agent session ends.
/// </summary>
public sealed record SessionEnded(
        [property: JsonPropertyName("reason")]    string?        Reason,
        [property: JsonPropertyName("timestamp")] DateTimeOffset Timestamp
    );
