namespace Kurrent.Agent.Schema;

/// <summary>
/// Token-usage metadata ridng on KurrentDB event metadata under the
/// <c>$usage</c> key on every assistant event. Not a canonical payload.
/// See <c>schema/SCHEMA_v2.md §3.6</c>.
/// </summary>
public sealed record TokenUsage(
    long?   InputTokens,
    long?   OutputTokens,
    long?   TotalTokens,
    long?   CachedInputTokens,
    long?   ReasoningTokens,
    string? Model
);

/// <summary>
/// Shared metadata key under which <see cref="TokenUsage"/> is stored.
/// </summary>
public static class UsageMetadata {
    public const string Key = "$usage";
}
