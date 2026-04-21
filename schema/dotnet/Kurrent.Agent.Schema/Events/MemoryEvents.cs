using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>A retained fact. Written to <c>AgentMemory-{app_name}-{user_id}</c>.</summary>
public sealed record FactRetained(
    string                                     Fact,
    DateTimeOffset                             RetainedAt,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
