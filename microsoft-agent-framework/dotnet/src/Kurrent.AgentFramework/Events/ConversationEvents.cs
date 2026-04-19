using System.Text.Json;
using System.Text.Json.Serialization;

namespace Kurrent.AgentFramework.Events;

/// <summary>
/// Emitted for each user message received by the agent.
/// </summary>
public sealed record UserMessageReceived(
        [property: JsonPropertyName("content")]       string?         Content,
        [property: JsonPropertyName("message_id")]    string?         MessageId,
        [property: JsonPropertyName("author_name")]   string?         AuthorName,
        [property: JsonPropertyName("created_at")]    DateTimeOffset? CreatedAt,
        [property: JsonPropertyName("message_index")] int             MessageIndex,
        [property: JsonPropertyName("timestamp")]     DateTimeOffset  Timestamp
    );

/// <summary>
/// Emitted when the assistant produces a text response (no tool calls).
/// </summary>
public sealed record AssistantTextGenerated(
        [property: JsonPropertyName("content")]       string?         Content,
        [property: JsonPropertyName("message_id")]    string?         MessageId,
        [property: JsonPropertyName("author_name")]   string?         AuthorName,
        [property: JsonPropertyName("created_at")]    DateTimeOffset? CreatedAt,
        [property: JsonPropertyName("message_index")] int             MessageIndex,
        [property: JsonPropertyName("timestamp")]     DateTimeOffset  Timestamp
    );

/// <summary>
/// Emitted when the assistant decides to call one or more tools.
/// One event per assistant message that contains function calls.
/// </summary>
public sealed record AssistantToolCallsGenerated(
        [property: JsonPropertyName("tool_calls")]    IReadOnlyList<ToolCallInfo> ToolCalls,
        [property: JsonPropertyName("content")]       string?                     Content,
        [property: JsonPropertyName("message_id")]    string?                     MessageId,
        [property: JsonPropertyName("author_name")]   string?                     AuthorName,
        [property: JsonPropertyName("created_at")]    DateTimeOffset?             CreatedAt,
        [property: JsonPropertyName("message_index")] int                         MessageIndex,
        [property: JsonPropertyName("timestamp")]     DateTimeOffset              Timestamp
    );

/// <summary>
/// A single tool call within an assistant message.
/// </summary>
public sealed record ToolCallInfo(
        [property: JsonPropertyName("call_id")]   string       CallId,
        [property: JsonPropertyName("tool_name")] string       ToolName,
        [property: JsonPropertyName("arguments")] JsonElement? Arguments
    );

/// <summary>
/// Emitted for each tool result message.
/// </summary>
public sealed record ToolResultReceived(
        [property: JsonPropertyName("call_id")]       string          CallId,
        [property: JsonPropertyName("tool_name")]     string?         ToolName,
        [property: JsonPropertyName("result")]        string?         Result,
        [property: JsonPropertyName("message_id")]    string?         MessageId,
        [property: JsonPropertyName("author_name")]   string?         AuthorName,
        [property: JsonPropertyName("created_at")]    DateTimeOffset? CreatedAt,
        [property: JsonPropertyName("message_index")] int             MessageIndex,
        [property: JsonPropertyName("timestamp")]     DateTimeOffset  Timestamp
    );
