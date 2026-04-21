using System.Text.Json.Serialization;

namespace Kurrent.AgentFramework.Events;

/// <summary>
/// Emitted when a fact is retained in agent memory.
/// </summary>
public sealed record FactRetained(
        [property: JsonPropertyName("fact")]        string         Fact,
        [property: JsonPropertyName("retained_at")] DateTimeOffset RetainedAt
    );
