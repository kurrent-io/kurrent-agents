using System.Text.Json;

namespace Kurrent.Agent.Schema;

/// <summary>
/// Token-usage metadata riding on KurrentDB event metadata under the
/// <c>$usage</c> key on every assistant event. Not a canonical payload.
/// See <c>schema/SCHEMA_v2.md §3.6</c>.
/// <para>
/// <c>AdditionalCounts</c> is an open bucket for provider-specific counters
/// that don't map onto the canonical slots (Anthropic's
/// <c>cache_creation_input_tokens</c> / <c>server_tool_use</c> /
/// <c>service_tier</c>, OpenAI's reasoning breakdown, …). MAF .NET already
/// emits this field via <c>UsageDetails.AdditionalCounts</c>.
/// </para>
/// </summary>
public sealed record TokenUsage(
    long?                                      InputTokens,
    long?                                      OutputTokens,
    long?                                      TotalTokens,
    long?                                      CachedInputTokens,
    long?                                      ReasoningTokens,
    string?                                    Model,
    IReadOnlyDictionary<string, JsonElement>?  AdditionalCounts = null
);

/// <summary>
/// Shared metadata key under which <see cref="TokenUsage"/> is stored.
/// </summary>
public static class UsageMetadata {
    public const string Key = "$usage";
}
