using System.Text.Json.Nodes;

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
/// <para>
/// <c>AdditionalCounts</c> is modelled as a <see cref="JsonObject"/> rather
/// than an <c>IDictionary</c> on purpose: <see cref="SchemaJsonOptions.Default"/>
/// applies <c>DictionaryKeyPolicy = SnakeCaseLower</c>, which would rewrite
/// provider-specific keys on write. Using <see cref="JsonObject"/> bypasses
/// that policy and preserves keys byte-for-byte, matching Python's
/// <c>dict[str, Any]</c> behaviour for cross-language round-trip parity.
/// </para>
/// <para>
/// Added as an init-only property (not a primary-constructor parameter) so
/// the positional record's compiler-generated <c>Deconstruct</c> keeps its
/// original 6-value shape — downstream <c>var (i, o, t, c, r, m) = usage;</c>
/// destructuring continues to compile.
/// </para>
/// </summary>
public sealed record TokenUsage(
    long?   InputTokens,
    long?   OutputTokens,
    long?   TotalTokens,
    long?   CachedInputTokens,
    long?   ReasoningTokens,
    string? Model
) {
    public JsonObject? AdditionalCounts { get; init; }
}

/// <summary>
/// Shared metadata key under which <see cref="TokenUsage"/> is stored.
/// </summary>
public static class UsageMetadata {
    public const string Key = "$usage";
}
