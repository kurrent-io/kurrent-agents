using System.Text.Json.Serialization;

namespace Kurrent.AgentFramework.Events;

/// <summary>
/// Emitted after each agent run with token usage telemetry.
/// </summary>
public sealed record TokenUsageRecorded(
        [property: JsonPropertyName("input_tokens")]        long?                              InputTokens,
        [property: JsonPropertyName("output_tokens")]       long?                              OutputTokens,
        [property: JsonPropertyName("total_tokens")]        long?                              TotalTokens,
        [property: JsonPropertyName("cached_input_tokens")] long?                              CachedInputTokens,
        [property: JsonPropertyName("reasoning_tokens")]    long?                              ReasoningTokens,
        [property: JsonPropertyName("additional_counts")]   IReadOnlyDictionary<string, long>? AdditionalCounts,
        [property: JsonPropertyName("model")]               string?                            Model,
        [property: JsonPropertyName("finish_reason")]       string?                            FinishReason,
        [property: JsonPropertyName("response_id")]         string?                            ResponseId,
        [property: JsonPropertyName("timestamp")]           DateTimeOffset                     Timestamp
    );
