using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>Emitted for each user message received by the agent.</summary>
public sealed record UserMessageReceived(
    string?                                    Content,
    string?                                    MessageId,
    string?                                    AuthorName,
    DateTimeOffset?                            CreatedAt,
    int                                        MessageIndex,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Emitted when the assistant produces a text response with no tool calls.</summary>
public sealed record AssistantTextGenerated(
    string?                                    Content,
    string?                                    MessageId,
    string?                                    AuthorName,
    DateTimeOffset?                            CreatedAt,
    int                                        MessageIndex,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Emitted when the assistant invokes one or more tools.</summary>
public sealed record AssistantToolCallsGenerated(
    IReadOnlyList<ToolCallInfo>                ToolCalls,
    string?                                    Content,
    string?                                    MessageId,
    string?                                    AuthorName,
    DateTimeOffset?                            CreatedAt,
    int                                        MessageIndex,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>Emitted per tool response.</summary>
public sealed record ToolResultReceived(
    string                                     CallId,
    string?                                    ToolName,
    string?                                    Result,
    string?                                    MessageId,
    string?                                    AuthorName,
    DateTimeOffset?                            CreatedAt,
    int                                        MessageIndex,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);

/// <summary>
/// Assistant reasoning output (extended thinking / o-series / Gemini thinking).
/// Plaintext in <c>Content</c> for Claude and Gemini; OpenAI o-series sets
/// <c>Encrypted=true</c> with the opaque blob in
/// <c>extensions.openai.thinking.raw</c> and the provider signature on
/// <see cref="Signature"/>. New in v2. See SCHEMA_v2 §3.2.
/// </summary>
public sealed record AssistantThinkingGenerated(
    string?                                    Content,
    bool                                       Encrypted,
    string?                                    Signature,
    string?                                    MessageId,
    string?                                    AuthorName,
    DateTimeOffset?                            CreatedAt,
    int                                        MessageIndex,
    DateTimeOffset                             Timestamp,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
